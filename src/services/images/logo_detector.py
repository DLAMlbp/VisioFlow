from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np

from src.core.config import Settings
from src.services.images.mosaic import Box
from src.services.profiles import LogoMosaicConfig


@dataclass(frozen=True)
class LogoDetection:
    box: Box
    confidence: float
    label: str = "dangjia_logo"
    product_logo: bool = False


class LogoDetector(Protocol):
    model_version: str

    def detect(
        self, image_bgr: np.ndarray, config: LogoMosaicConfig
    ) -> list[LogoDetection]: ...


class UnavailableLogoDetector:
    """Fail-safe placeholder used until a verified model is configured."""

    model_version = "unavailable"

    def detect(
        self, image_bgr: np.ndarray, config: LogoMosaicConfig
    ) -> list[LogoDetection]:
        del image_bgr, config
        raise RuntimeError("dangjia logo detector model is not configured")


class RapidOcrDangjiaLogoDetector:
    """Detect the textual Dangjia brand lockup using the shared OCR runtime."""

    model_version = "dangjia-rapidocr-v1"

    def detect(
        self, image_bgr: np.ndarray, config: LogoMosaicConfig
    ) -> list[LogoDetection]:
        from src.services.images.adapters.rapidocr import detect_text_lines

        image_height, image_width = image_bgr.shape[:2]
        detections: list[LogoDetection] = []
        for line in detect_text_lines(image_bgr):
            compact = "".join(line.text.upper().split())
            # Rotated shirt prints commonly make the final "PP" look like
            # "P" or "F" to OCR. Accept those variants only when they follow
            # the Dangjia brand name, avoiding generic APP-text false positives.
            app_wordmark = any(
                token in compact for token in ("当家APP", "当家AP", "当家AF")
            )
            if not app_wordmark and "当家" not in compact:
                continue
            # Transparent mascot overlays target only the APP token. A plain
            # "当家" occurrence is intentionally left untouched.
            if (
                config.action == "overlay_asset"
                and config.target_component == "app_text"
                and not app_wordmark
            ):
                continue
            if line.confidence < config.confidence:
                continue
            xs = line.polygon[:, 0]
            ys = line.polygon[:, 1]
            x0, x1 = float(xs.min()), float(xs.max())
            y0, y1 = float(ys.min()), float(ys.max())
            if len(line.polygon) >= 4:
                points = np.asarray(line.polygon[:4], dtype=np.float32)
                text_width = max(
                    1.0,
                    float(
                        (
                            np.linalg.norm(points[1] - points[0])
                            + np.linalg.norm(points[2] - points[3])
                        )
                        / 2
                    ),
                )
                text_height = max(
                    1.0,
                    float(
                        (
                            np.linalg.norm(points[3] - points[0])
                            + np.linalg.norm(points[2] - points[1])
                        )
                        / 2
                    ),
                )
            else:
                text_width = max(1.0, x1 - x0)
                text_height = max(1.0, y1 - y0)
            # Dangjia's icon sits to the left and its small tagline below the OCR
            # baseline. Use oriented edge lengths because a rotated shirt logo's
            # axis-aligned height would otherwise produce a very large false box.
            x0 -= max(text_width * 0.28, text_height * 0.90)
            y0 -= text_height * 0.10
            y1 += text_height * 0.35
            detections.append(
                LogoDetection(
                    box=(
                        max(0, round(x0)),
                        max(0, round(y0)),
                        min(image_width, round(x1)),
                        min(image_height, round(y1)),
                    ),
                    confidence=line.confidence,
                )
            )
        return detections


class HybridLogoDetector:
    """Use the first healthy detector and fall back only on runtime failure.

    Once a validated YOLOX model is present, mixing OCR candidates back into its
    output would invalidate the detector's measured precision. An empty primary
    result is therefore authoritative; OCR is used only while weights are absent
    or when the primary adapter raises a runtime availability error.
    """

    def __init__(self, detectors: list[LogoDetector]) -> None:
        self.detectors = detectors
        self.model_version = "+".join(item.model_version for item in detectors)

    def detect(
        self, image_bgr: np.ndarray, config: LogoMosaicConfig
    ) -> list[LogoDetection]:
        for detector in self.detectors:
            try:
                candidates = detector.detect(image_bgr, config)
            except RuntimeError:
                continue
            candidates.sort(key=lambda item: item.confidence, reverse=True)
            kept: list[LogoDetection] = []
            for candidate in candidates:
                if all(
                    _box_iou(candidate.box, item.box) <= config.nms_iou
                    for item in kept
                ):
                    kept.append(candidate)
            return kept
        raise RuntimeError("all dangjia logo detectors are unavailable")


def _box_iou(first: Box, second: Box) -> float:
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[2], second[2])
    y1 = min(first[3], second[3])
    intersection = max(0, x1 - x0) * max(0, y1 - y0)
    first_area = max(0, first[2] - first[0]) * max(0, first[3] - first[1])
    second_area = max(0, second[2] - second[0]) * max(0, second[3] - second[1])
    return intersection / max(1, first_area + second_area - intersection)


def load_logo_detector(settings: Settings) -> LogoDetector:
    manifest = (
        Path(settings.redaction_models_directory)
        / "logos"
        / "dangjia"
        / "v1"
        / "manifest.json"
    )
    return _cached_logo_detector(str(manifest), settings.redaction_onnx_threads)


@lru_cache(maxsize=4)
def _cached_logo_detector(manifest_path: str, threads: int) -> LogoDetector:
    from src.services.images.adapters.yolox_onnx import YoloXOnnxLogoDetector

    detectors: list[LogoDetector] = []
    manifest = Path(manifest_path)
    if manifest.is_file():
        import json

        payload = json.loads(manifest.read_text(encoding="utf-8"))
        model_path = manifest.parent / str(payload.get("model", ""))
        if model_path.is_file():
            detectors.append(
                YoloXOnnxLogoDetector(manifest, intra_op_threads=threads)
            )
    detectors.append(RapidOcrDangjiaLogoDetector())
    return HybridLogoDetector(detectors)
