from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Protocol

import cv2
import numpy as np

from src.services.images.logo_detector import LogoDetector, UnavailableLogoDetector
from src.services.images.logo_overlay import apply_logo_overlays
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
        if config.post_action == "keep":
            return ImageStageResult(
                image_bgr=image_bgr.copy(),
                audit={
                    "enabled": True,
                    "status": "kept_by_policy",
                    "allow_during_filter": config.allow_during_filter,
                },
                reasons=("左下角拍摄水印按标准允许通过并保留",),
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
            detection_boxes = [detection.box for detection in detections]
            asset_sha256: str | None = None
            if config.action == "overlay_asset":
                # Detectors return the whole brand lockup. Preserve the left
                # icon and the Chinese word "当家"; cover only the right-hand
                # APP token with the transparent Xiaodang artwork.
                if config.target_component == "app_text":
                    detection_boxes = [_app_token_box(box) for box in detection_boxes]
                result, rendered_boxes, asset_sha256 = apply_logo_overlays(
                    image_bgr,
                    detection_boxes,
                    asset_id=config.overlay_asset_id,
                    expansion=(
                        0.0
                        if config.target_component == "app_text"
                        else config.box_expansion
                    ),
                    scale=config.overlay_scale,
                )
                applied_boxes = detection_boxes if rendered_boxes else []
            else:
                result, rendered_boxes = apply_pixel_mosaic(
                    image_bgr,
                    detection_boxes,
                    expansion=config.box_expansion,
                    block_ratio=config.mosaic_block_ratio,
                )
                applied_boxes = rendered_boxes
            return ImageStageResult(
                image_bgr=result,
                audit={
                    "enabled": True,
                    "status": "applied" if applied_boxes else "not_detected",
                    "image_size": [image_width, image_height],
                    "model_version": self.logo_detector.model_version,
                    "detections": len(applied_boxes),
                    "boxes": [list(box) for box in applied_boxes],
                    "rendered_boxes": [list(box) for box in rendered_boxes],
                    "confidences": [round(item.confidence, 4) for item in detections],
                    "action": config.action,
                    "target_component": config.target_component,
                    "overlay_asset_id": (
                        config.overlay_asset_id if config.action == "overlay_asset" else None
                    ),
                    "overlay_asset_sha256": asset_sha256,
                    "modified_area_ratio": round(
                        boxes_area_ratio(image_bgr.shape, rendered_boxes), 6
                    ),
                    "outside_boxes_changed_pixels": changed_pixels_outside_boxes(
                        image_bgr, result, rendered_boxes
                    ),
                    "duration_ms": round((perf_counter() - started) * 1000),
                },
                reasons=(
                    (
                        "已保留当家品牌名称并使用小当图标遮挡 APP 字样"
                        if config.action == "overlay_asset"
                        else "已自动识别并使用马赛克遮挡画面中的当家 APP Logo"
                    ),
                )
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


def _app_token_box(box: Box) -> Box:
    x0, y0, x1, y1 = box
    width = x1 - x0
    height = y1 - y0
    if width <= 0 or height <= 0 or width < height * 1.25:
        return box
    return (x0 + round(width * 0.55), y0, x1, y1)


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
