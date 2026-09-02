from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol

import cv2
import numpy as np

from src.services.images.logo_detector import LogoDetector, UnavailableLogoDetector
from src.services.images.mosaic import Box, apply_pixel_mosaic
from src.services.profiles import LogoMosaicConfig, WatermarkRemovalConfig


@dataclass(frozen=True)
class ImageStageResult:
    image_bgr: np.ndarray
    audit: dict[str, Any]
    reasons: tuple[str, ...] = field(default_factory=tuple)


class WatermarkProcessor(Protocol):
    profile_version: str

    def remove(
        self, image_bgr: np.ndarray, config: WatermarkRemovalConfig
    ) -> ImageStageResult: ...


class UnavailableWatermarkProcessor:
    profile_version = "unavailable"

    def remove(
        self, image_bgr: np.ndarray, config: WatermarkRemovalConfig
    ) -> ImageStageResult:
        del image_bgr, config
        raise RuntimeError("dangjia watermark profile is not configured")


def normalized_roi_to_box(
    roi: tuple[float, float, float, float],
    *,
    image_width: int,
    image_height: int,
) -> Box:
    x0, y0, x1, y1 = roi
    box = (
        max(0, min(image_width, round(x0 * image_width))),
        max(0, min(image_height, round(y0 * image_height))),
        max(0, min(image_width, round(x1 * image_width))),
        max(0, min(image_height, round(y1 * image_height))),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("watermark ROI resolves to an empty pixel region")
    return box


def changed_pixels_outside_box(before: np.ndarray, after: np.ndarray, box: Box) -> int:
    if before.shape != after.shape:
        raise ValueError("images must have identical shapes")
    changed = np.any(before != after, axis=2) if before.ndim == 3 else before != after
    x0, y0, x1, y1 = box
    changed[y0:y1, x0:x1] = False
    return int(np.count_nonzero(changed))


def changed_pixels_outside_boxes(
    before: np.ndarray, after: np.ndarray, boxes: list[Box]
) -> int:
    """Count pixel changes not covered by any exact applied Logo box."""
    if before.shape != after.shape:
        raise ValueError("images must have identical shapes")
    changed = np.any(before != after, axis=2) if before.ndim == 3 else before != after
    for x0, y0, x1, y1 in boxes:
        changed[y0:y1, x0:x1] = False
    return int(np.count_nonzero(changed))


def boxes_area_ratio(image_shape: tuple[int, ...], boxes: list[Box]) -> float:
    """Return union area of applied boxes without double-counting overlaps."""
    height, width = image_shape[:2]
    covered = np.zeros((height, width), dtype=np.uint8)
    for x0, y0, x1, y1 in boxes:
        covered[y0:y1, x0:x1] = 1
    return float(np.count_nonzero(covered)) / float(max(1, height * width))


class ImageRedactionService:
    def __init__(
        self,
        *,
        watermark_processor: WatermarkProcessor | None = None,
        logo_detector: LogoDetector | None = None,
    ) -> None:
        self.watermark_processor = watermark_processor or UnavailableWatermarkProcessor()
        self.logo_detector = logo_detector or UnavailableLogoDetector()

    def remove_watermark(
        self, image_bgr: np.ndarray, config: WatermarkRemovalConfig
    ) -> ImageStageResult:
        if not config.enabled:
            return ImageStageResult(
                image_bgr=image_bgr.copy(),
                audit={"enabled": False, "status": "disabled"},
            )
        started = perf_counter()
        image_height, image_width = image_bgr.shape[:2]
        try:
            result = self.watermark_processor.remove(image_bgr, config)
            audit = dict(result.audit)
            audit.update(
                {
                    "enabled": True,
                    "profile_version": self.watermark_processor.profile_version,
                    "duration_ms": round((perf_counter() - started) * 1000),
                }
            )
            return ImageStageResult(result.image_bgr, audit, result.reasons)
        # Third-party image/model adapters have heterogeneous exception types. This
        # boundary deliberately converts all of them into a fail-safe no-op result.
        except Exception as exc:  # noqa: BLE001
            return ImageStageResult(
                image_bgr=image_bgr.copy(),
                audit={
                    "enabled": True,
                    "status": "failed_safe",
                    "image_size": [image_width, image_height],
                    "profile_version": self.watermark_processor.profile_version,
                    "duration_ms": round((perf_counter() - started) * 1000),
                    "error": str(exc)[:300],
                },
                reasons=("左下角水印处理失败，已安全保留原画面",),
            )

    def mosaic_logos(
        self, image_bgr: np.ndarray, config: LogoMosaicConfig
    ) -> ImageStageResult:
        if not config.enabled:
            return ImageStageResult(
                image_bgr=image_bgr.copy(),
                audit={"enabled": False, "status": "disabled"},
            )
        started = perf_counter()
        image_height, image_width = image_bgr.shape[:2]
        try:
            detections = [
                detection
                for detection in self.logo_detector.detect(image_bgr, config)
                if detection.label in config.targets
                and detection.confidence >= config.confidence
                and (config.include_product_logos or not detection.product_logo)
            ]
            result, applied_boxes = apply_pixel_mosaic(
                image_bgr,
                [detection.box for detection in detections],
                expansion=config.box_expansion,
                block_ratio=config.mosaic_block_ratio,
            )
            return ImageStageResult(
                image_bgr=result,
                audit={
                    "enabled": True,
                    "status": "applied" if applied_boxes else "not_detected",
                    "image_size": [image_width, image_height],
                    "model_version": self.logo_detector.model_version,
                    "detections": len(applied_boxes),
                    "boxes": [list(box) for box in applied_boxes],
                    "confidences": [round(item.confidence, 4) for item in detections],
                    "modified_area_ratio": round(
                        boxes_area_ratio(image_bgr.shape, applied_boxes), 6
                    ),
                    "outside_boxes_changed_pixels": changed_pixels_outside_boxes(
                        image_bgr, result, applied_boxes
                    ),
                    "duration_ms": round((perf_counter() - started) * 1000),
                },
                reasons=("已自动识别并遮挡画面中的当家 APP Logo",)
                if applied_boxes
                else (),
            )
        # Detection must never make an otherwise valid image job fail.
        except Exception as exc:  # noqa: BLE001
            return ImageStageResult(
                image_bgr=image_bgr.copy(),
                audit={
                    "enabled": True,
                    "status": "failed_safe",
                    "image_size": [image_width, image_height],
                    "model_version": self.logo_detector.model_version,
                    "duration_ms": round((perf_counter() - started) * 1000),
                    "error": str(exc)[:300],
                },
                reasons=("Logo 自动遮挡不可用，已安全保留当前画面",),
            )


def decode_image(image_bytes: bytes) -> np.ndarray:
    data = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("unable to decode image")
    return image


def encode_jpeg(image_bgr: np.ndarray, *, quality: int = 95) -> bytes:
    ok, encoded = cv2.imencode(
        ".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
    )
    if not ok:
        raise ValueError("unable to encode JPEG")
    return encoded.tobytes()
