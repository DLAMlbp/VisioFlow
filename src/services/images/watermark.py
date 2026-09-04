from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.core.config import Settings
from src.services.images.adapters.lama_opencv import erase_lama_opencv
from src.services.images.adapters.migan_openvino import erase_migan_openvino
from src.services.images.adapters.rapidocr import detect_text_lines, detect_text_polygons
from src.services.images.adapters.remove_ai_watermarks import erase_mask
from src.services.images.mosaic import Box
from src.services.images.redaction import (
    ImageStageResult,
    changed_pixels_outside_box,
    normalized_roi_to_box,
)
from src.services.profiles import WatermarkRemovalConfig


@dataclass(frozen=True)
class WatermarkPreparation:
    image_bgr: np.ndarray
    mask: np.ndarray
    audit: dict[str, Any]
    requires_inpaint: bool
    reasons: tuple[str, ...] = ()


_APP_TOKEN = re.compile(r"(?<![A-Z])A\s*P\s*P(?![A-Z])", re.IGNORECASE)


def app_token_box_from_text_line(
    polygon: np.ndarray,
    text: str,
    *,
    offset: tuple[int, int] = (0, 0),
) -> Box | None:
    """Project the exact APP substring from an OCR line into an axis-aligned box."""
    match = _APP_TOKEN.search(text)
    points = np.asarray(polygon, dtype=np.float32)
    if match is None or points.shape != (4, 2) or not text:
        return None
    start_ratio = match.start() / len(text)
    end_ratio = match.end() / len(text)
    top_left, top_right, bottom_right, bottom_left = points
    token_points = np.asarray(
        [
            top_left + (top_right - top_left) * start_ratio,
            top_left + (top_right - top_left) * end_ratio,
            bottom_left + (bottom_right - bottom_left) * end_ratio,
            bottom_left + (bottom_right - bottom_left) * start_ratio,
        ]
    )
    xs, ys = token_points[:, 0], token_points[:, 1]
    token_height = max(1.0, float(ys.max() - ys.min()))
    padding = token_height * 0.08
    offset_x, offset_y = offset
    return (
        round(float(xs.min() - padding)) + offset_x,
        round(float(ys.min() - padding)) + offset_y,
        round(float(xs.max() + padding)) + offset_x,
        round(float(ys.max() + padding)) + offset_y,
    )


def detect_watermark_app_boxes(
    image_bgr: np.ndarray,
    config: WatermarkRemovalConfig,
) -> tuple[list[Box], list[float], Box]:
    """Detect only explicit APP tokens inside the protected bottom-left ROI."""
    height, width = image_bgr.shape[:2]
    roi_box = normalized_roi_to_box(
        config.roi, image_width=width, image_height=height
    )
    x0, y0, x1, y1 = roi_box
    boxes: list[Box] = []
    confidences: list[float] = []
    for line in detect_text_lines(image_bgr[y0:y1, x0:x1]):
        if line.confidence < config.detection_threshold:
            continue
        box = app_token_box_from_text_line(
            line.polygon,
            line.text,
            offset=(x0, y0),
        )
        if box is None:
            continue
        clipped = (
            max(x0, box[0]),
            max(y0, box[1]),
            min(x1, box[2]),
            min(y1, box[3]),
        )
        if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
            continue
        boxes.append(clipped)
        confidences.append(round(line.confidence, 4))
    return boxes, confidences, roi_box


def reverse_alpha_blend(
    image_bgr: np.ndarray,
    alpha_map: np.ndarray,
    *,
    position: tuple[int, int],
    foreground_bgr: tuple[float, float, float] = (255.0, 255.0, 255.0),
    alpha_threshold: float = 0.002,
    max_alpha: float = 0.99,
) -> tuple[np.ndarray, np.ndarray]:
    """Reverse a known overlay using GeminiWatermarkTool's blending equation.

    watermarked = alpha * foreground + (1-alpha) * original
    original = (watermarked-alpha*foreground) / (1-alpha)
    """
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("reverse alpha blending requires a BGR image")
    if alpha_map.ndim != 2:
        raise ValueError("alpha map must be single-channel")
    result = image_bgr.copy()
    full_mask = np.zeros(image_bgr.shape[:2], dtype=np.uint8)
    image_height, image_width = image_bgr.shape[:2]
    x, y = position
    map_height, map_width = alpha_map.shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(image_width, x + map_width), min(image_height, y + map_height)
    if x1 <= x0 or y1 <= y0:
        return result, full_mask

    alpha = alpha_map[y0 - y : y1 - y, x0 - x : x1 - x].astype(np.float32)
    alpha = np.clip(alpha, 0, max_alpha)
    active = alpha >= alpha_threshold
    if not np.any(active):
        return result, full_mask
    region = result[y0:y1, x0:x1].astype(np.float32)
    foreground = np.asarray(foreground_bgr, dtype=np.float32).reshape(1, 1, 3)
    divisor = np.maximum(1.0 - alpha[..., None], 1.0 - max_alpha)
    restored = (region - alpha[..., None] * foreground) / divisor
    restored = np.clip(restored, 0, 255).astype(np.uint8)
    region_u8 = result[y0:y1, x0:x1]
    region_u8[active] = restored[active]
    result[y0:y1, x0:x1] = region_u8
    full_mask[y0:y1, x0:x1][active] = 255
    return result, full_mask


def estimate_alpha_map(
    clean_bgr: np.ndarray,
    watermarked_bgr: np.ndarray,
    *,
    foreground_bgr: tuple[float, float, float] = (255.0, 255.0, 255.0),
) -> np.ndarray:
    """Estimate a per-pixel alpha map from an authorized clean/overlay pair."""
    if clean_bgr.shape != watermarked_bgr.shape:
        raise ValueError("calibration images must have identical shapes")
    clean = clean_bgr.astype(np.float32)
    marked = watermarked_bgr.astype(np.float32)
    foreground = np.asarray(foreground_bgr, dtype=np.float32).reshape(1, 1, 3)
    denominator = foreground - clean
    valid = np.abs(denominator) >= 8
    estimates = np.divide(
        marked - clean,
        denominator,
        out=np.full_like(clean, np.nan),
        where=valid,
    )
    with np.errstate(invalid="ignore"):
        alpha = np.nanmedian(estimates, axis=2)
    return np.clip(np.nan_to_num(alpha, nan=0.0), 0.0, 0.99).astype(np.float32)


def build_text_candidate_mask(roi_bgr: np.ndarray) -> tuple[np.ndarray, float]:
    """Find watermark-like strokes inside the already protected ROI.

    This is deliberately a conservative fallback, not a full-image text detector.
    It looks for low-saturation, high-local-contrast strokes in the three known
    Dangjia overlay line bands and rejects implausibly large components.
    """
    height, width = roi_bgr.shape[:2]
    if height < 20 or width < 40:
        return np.zeros((height, width), np.uint8), 0.0
    gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    local_kernel_size = max(7, round(min(height, width) * 0.045))
    if local_kernel_size % 2 == 0:
        local_kernel_size += 1
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (local_kernel_size, local_kernel_size)
    )
    bright = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    dark = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    contrast = cv2.max(bright, dark)
    low_saturation = hsv[:, :, 1] <= 105
    candidate = ((contrast >= 11) & low_saturation).astype(np.uint8) * 255

    line_bands = np.zeros_like(candidate)
    for start, end in ((0.10, 0.42), (0.38, 0.73), (0.68, 1.0)):
        y0, y1 = round(height * start), round(height * end)
        line_bands[y0:y1, : round(width * 0.96)] = 255
    candidate = cv2.bitwise_and(candidate, line_bands)
    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 2))
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, close_kernel)

    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, 8)
    filtered = np.zeros_like(candidate)
    kept = 0
    min_area = max(3, round(height * width * 0.000015))
    for label in range(1, component_count):
        x, y, component_width, component_height, area = stats[label]
        del x, y
        if area < min_area:
            continue
        if component_height < 2 or component_height > height * 0.22:
            continue
        if component_width > width * 0.45:
            continue
        if area / max(1, component_width * component_height) > 0.90:
            continue
        filtered[labels == label] = 255
        kept += 1

    if kept:
        dilation = max(1, round(min(height, width) / 360))
        dilate_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * dilation + 1, 2 * dilation + 1)
        )
        filtered = cv2.dilate(filtered, dilate_kernel)
    area_ratio = float(np.count_nonzero(filtered)) / float(height * width)
    line_hits = sum(
        bool(np.any(filtered[round(height * start) : round(height * end)]))
        for start, end in ((0.10, 0.42), (0.38, 0.73), (0.68, 1.0))
    )
    component_score = min(1.0, kept / 24.0)
    area_score = min(1.0, area_ratio / 0.035) if area_ratio else 0.0
    confidence = 0.45 * component_score + 0.35 * (line_hits / 3.0) + 0.20 * area_score
    return filtered, float(min(1.0, confidence))


def build_ocr_text_mask(
    roi_bgr: np.ndarray, polygons: list[np.ndarray]
) -> tuple[np.ndarray, float]:
    """Refine OCR polygons to local text strokes instead of erasing rectangles."""
    height, width = roi_bgr.shape[:2]
    polygon_mask = np.zeros((height, width), dtype=np.uint8)
    valid_polygons: list[np.ndarray] = []
    for polygon in polygons:
        points = np.rint(polygon).astype(np.int32)
        points[:, 0] = np.clip(points[:, 0], 0, width - 1)
        points[:, 1] = np.clip(points[:, 1], 0, height - 1)
        if cv2.contourArea(points) < 20:
            continue
        cv2.fillPoly(polygon_mask, [points], 255)
        valid_polygons.append(points)

        # The known address line sometimes ends in repeated asterisks. OCR can
        # recognize the line while shortening its polygon before those symbols.
        # Extend only the *candidate-search corridor* for a wide second-line box;
        # the contrast/edge test below still selects glyph strokes, not the area.
        center_y = float(np.mean(points[:, 1])) / max(1, height)
        polygon_width = int(np.max(points[:, 0]) - np.min(points[:, 0]))
        if 0.38 <= center_y <= 0.73 and polygon_width >= width * 0.55:
            right_edge = min(width - 1, max(int(np.max(points[:, 0])), round(width * 0.90)))
            corridor = points.copy()
            right_indices = np.argsort(corridor[:, 0])[-2:]
            corridor[right_indices, 0] = right_edge
            cv2.fillPoly(polygon_mask, [corridor], 255)
    if not valid_polygons:
        return polygon_mask, 0.0

    gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
    blur_size = max(9, round(min(height, width) * 0.055))
    if blur_size % 2 == 0:
        blur_size += 1
    local = cv2.GaussianBlur(gray, (blur_size, blur_size), 0)
    delta = gray.astype(np.int16) - local.astype(np.int16)
    edges = cv2.Canny(gray, 24, 72)
    strokes = (
        ((delta >= 7) | (delta <= -10) | (edges > 0))
        & (polygon_mask > 0)
    ).astype(np.uint8) * 255
    # OCR coordinates scale with the decoded image, so a fixed 9x9 kernel leaves
    # antialiased punctuation and thin glyph tips behind on high-resolution phone
    # photos. Keep the original four-pixel radius at the reference 230px ROI and
    # scale it proportionally. This still refines strokes rather than filling the
    # complete OCR rectangles.
    stroke_radius = max(4, round(min(height, width) * 0.018))
    stroke_kernel_size = 2 * stroke_radius + 1
    strokes = cv2.dilate(
        strokes,
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (stroke_kernel_size, stroke_kernel_size)
        ),
    )

    centers = [float(np.mean(points[:, 1])) / max(1, height) for points in valid_polygons]
    line_hits = sum(
        any(start <= center <= end for center in centers)
        for start, end in ((0.10, 0.42), (0.38, 0.73), (0.68, 1.0))
    )
    confidence = min(1.0, 0.25 + 0.2 * len(valid_polygons) + 0.15 * line_hits)
    return strokes, float(confidence)


class DangjiaWatermarkProcessor:
    profile_version = "dangjia-watermark-v1"

    def __init__(self, profile_directory: str | Path = "models/watermarks/dangjia/v1") -> None:
        self.profile_directory = Path(profile_directory)
        self._manifest, self._alpha_map = self._load_profile()

    def _load_profile(self) -> tuple[dict[str, Any], np.ndarray | None]:
        manifest_path = self.profile_directory / "manifest.json"
        if not manifest_path.is_file():
            return {}, None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        alpha_path = self.profile_directory / str(manifest.get("alpha_map", ""))
        if not alpha_path.is_file():
            return manifest, None
        with np.load(alpha_path) as payload:
            alpha = np.asarray(payload["alpha"], dtype=np.float32)
        return manifest, alpha

    def prepare_app_overlay(
        self, image_bgr: np.ndarray, config: WatermarkRemovalConfig
    ) -> WatermarkPreparation:
        """Create render instructions without changing pixels or invoking inpainting."""
        height, width = image_bgr.shape[:2]
        boxes, confidences, roi_box = detect_watermark_app_boxes(image_bgr, config)
        return WatermarkPreparation(
            image_bgr=image_bgr.copy(),
            mask=np.zeros((height, width), dtype=np.uint8),
            audit={
                "enabled": True,
                "status": "detected" if boxes else "not_detected",
                "image_size": [width, height],
                "roi_px": list(roi_box),
                "automatic_boxes": [list(box) for box in boxes],
                "confidences": confidences,
                "method": "rapidocr_app_token",
                "outside_roi_changed_pixels": 0,
            },
            requires_inpaint=False,
            reasons=("已定位左下角拍摄水印中的 APP，等待使用小当图标遮挡",)
            if boxes
            else (),
        )

    def prepare(
        self, image_bgr: np.ndarray, config: WatermarkRemovalConfig
    ) -> WatermarkPreparation:
        height, width = image_bgr.shape[:2]
        roi_box = normalized_roi_to_box(
            config.roi, image_width=width, image_height=height
        )
        x0, y0, x1, y1 = roi_box
        result = image_bgr.copy()
        method = "stroke_mask_telea"
        confidence = 0.0
        mask = np.zeros((height, width), dtype=np.uint8)

        if self._alpha_map is not None:
            alpha = cv2.resize(
                self._alpha_map,
                (x1 - x0, y1 - y0),
                interpolation=cv2.INTER_LINEAR,
            )
            foreground = tuple(
                float(value)
                for value in self._manifest.get("foreground_bgr", [220, 220, 220])
            )
            result, mask = reverse_alpha_blend(
                result,
                alpha,
                position=(x0, y0),
                foreground_bgr=foreground,
            )
            confidence = 1.0
            method = "reverse_alpha"
        else:
            roi_image = result[y0:y1, x0:x1]
            polygons: list[np.ndarray] = []
            if config.roi_ocr_enabled:
                try:
                    polygons = detect_text_polygons(roi_image)
                except (ImportError, RuntimeError, ValueError):
                    polygons = []
            if polygons:
                roi_mask, confidence = build_ocr_text_mask(roi_image, polygons)
                # Recognition boxes can stop before repeated punctuation (for
                # example the masked address suffix). Supplement them with the
                # conservative, profile-specific stroke detector so those thin
                # glyphs are covered without turning a whole text line into a
                # rectangular repair region.
                candidate_mask, candidate_confidence = build_text_candidate_mask(
                    roi_image
                )
                if candidate_confidence >= config.detection_threshold:
                    roi_mask = cv2.bitwise_or(roi_mask, candidate_mask)
                    confidence = max(confidence, candidate_confidence)
                    method = "rapidocr_openvino+profile_strokes"
                else:
                    method = "rapidocr_openvino"
            else:
                roi_mask, confidence = build_text_candidate_mask(roi_image)
            mask[y0:y1, x0:x1] = roi_mask

        mask_area = int(np.count_nonzero(mask))
        mask_area_ratio = mask_area / float(max(1, (x1 - x0) * (y1 - y0)))
        if confidence < config.detection_threshold or mask_area == 0:
            return WatermarkPreparation(
                image_bgr=image_bgr.copy(),
                mask=mask,
                audit={
                    "status": "not_detected",
                    "image_size": [width, height],
                    "roi_px": list(roi_box),
                    "confidence": round(confidence, 4),
                    "mask_area_ratio": round(mask_area_ratio, 6),
                    "outside_roi_changed_pixels": 0,
                    "method": method,
                },
                requires_inpaint=False,
            )
        if mask_area_ratio > config.max_modified_ratio:
            raise ValueError("watermark mask exceeds the configured safety limit")

        if method == "reverse_alpha":
            outside_changed = changed_pixels_outside_box(image_bgr, result, roi_box)
            if config.preserve_outside_roi and outside_changed:
                raise ValueError("watermark processor modified pixels outside the protected ROI")
            return WatermarkPreparation(
                image_bgr=result,
                mask=mask,
                audit={
                    "status": "applied",
                    "image_size": [width, height],
                    "roi_px": list(roi_box),
                    "confidence": round(confidence, 4),
                    "mask_area_ratio": round(mask_area_ratio, 6),
                    "outside_roi_changed_pixels": outside_changed,
                    "method": method,
                },
                requires_inpaint=False,
                reasons=("已在受保护的左下角区域去除当家 APP 拍摄水印",),
            )

        return WatermarkPreparation(
            image_bgr=image_bgr.copy(),
            mask=mask,
            audit={
                "status": "detected",
                "image_size": [width, height],
                "roi_px": list(roi_box),
                "confidence": round(confidence, 4),
                "mask_area_ratio": round(mask_area_ratio, 6),
                "outside_roi_changed_pixels": 0,
                "method": method,
            },
            requires_inpaint=True,
        )

    def apply_prepared(
        self,
        preparation: WatermarkPreparation,
        config: WatermarkRemovalConfig,
    ) -> ImageStageResult:
        if not preparation.requires_inpaint:
            return ImageStageResult(
                preparation.image_bgr.copy(),
                dict(preparation.audit),
                preparation.reasons,
            )

        image_bgr = preparation.image_bgr
        mask = preparation.mask
        result = image_bgr.copy()
        method = str(preparation.audit.get("method") or "stroke_mask_telea")

        if config.backend in {"deblend_then_lama", "lama"}:
            model_name = str(
                self._manifest.get("lama_model") or "inpainting_lama_2025jan.onnx"
            )
            model_path = self.profile_directory / model_name
            try:
                result = erase_lama_opencv(result, mask, model_path=model_path)
                method = f"{method}+lama_opencv"
            except RuntimeError:
                migan_name = str(self._manifest.get("migan_model") or "migan.onnx")
                migan_path = self.profile_directory / migan_name
                try:
                    result = erase_migan_openvino(result, mask, model_path=migan_path)
                    method = f"{method}+migan_fallback"
                except RuntimeError:
                    result = erase_mask(
                        result,
                        mask,
                        backend="cv2",
                        radius=config.inpaint_radius,
                    )
                    method = f"{method}+telea_fallback"
        elif config.backend in {"deblend_then_migan", "migan"}:
            model_name = str(self._manifest.get("migan_model") or "migan.onnx")
            model_path = self.profile_directory / model_name
            try:
                result = erase_migan_openvino(result, mask, model_path=model_path)
                method = f"{method}+migan_openvino"
            except RuntimeError:
                result = erase_mask(
                    result,
                    mask,
                    backend="cv2",
                    radius=config.inpaint_radius,
                )
                method = f"{method}+telea_fallback"
        elif config.backend == "deblend_then_telea":
            result = erase_mask(
                result,
                mask,
                backend="cv2",
                radius=config.inpaint_radius,
            )
            method = f"{method}+telea"

        roi_values = preparation.audit.get("roi_px")
        if not isinstance(roi_values, list) or len(roi_values) != 4:
            raise ValueError("prepared watermark ROI is missing")
        roi_box = tuple(int(value) for value in roi_values)
        outside_changed = changed_pixels_outside_box(image_bgr, result, roi_box)
        if config.preserve_outside_roi and outside_changed:
            raise ValueError("watermark processor modified pixels outside the protected ROI")
        audit = dict(preparation.audit)
        audit.update(
            {
                "status": "applied",
                "outside_roi_changed_pixels": outside_changed,
                "method": method,
            }
        )
        return ImageStageResult(
            image_bgr=result,
            audit=audit,
            reasons=("已在受保护的左下角区域去除当家 APP 拍摄水印",),
        )

    def remove(
        self, image_bgr: np.ndarray, config: WatermarkRemovalConfig
    ) -> ImageStageResult:
        return self.apply_prepared(self.prepare(image_bgr, config), config)


@lru_cache(maxsize=4)
def _cached_watermark_processor(profile_directory: str) -> DangjiaWatermarkProcessor:
    return DangjiaWatermarkProcessor(profile_directory)


def load_watermark_processor(settings: Settings) -> DangjiaWatermarkProcessor:
    profile_directory = (
        Path(settings.redaction_models_directory) / "watermarks" / "dangjia" / "v1"
    )
    return _cached_watermark_processor(str(profile_directory))
