from pathlib import Path

import cv2
import numpy as np

from src.services.images.watermark import (
    DangjiaWatermarkProcessor,
    build_ocr_text_mask,
    build_text_candidate_mask,
    estimate_alpha_map,
    reverse_alpha_blend,
)
from src.services.profiles import WatermarkRemovalConfig


def _apply_overlay(
    clean: np.ndarray,
    alpha: np.ndarray,
    position: tuple[int, int],
    foreground: tuple[int, int, int],
) -> np.ndarray:
    result = clean.copy()
    x, y = position
    height, width = alpha.shape
    region = result[y : y + height, x : x + width].astype(np.float32)
    foreground_array = np.asarray(foreground, dtype=np.float32).reshape(1, 1, 3)
    marked = alpha[..., None] * foreground_array + (1 - alpha[..., None]) * region
    result[y : y + height, x : x + width] = np.clip(marked, 0, 255).astype(np.uint8)
    return result


def test_reverse_alpha_blend_restores_a_known_overlay() -> None:
    clean = np.full((80, 100, 3), (45, 90, 130), dtype=np.uint8)
    alpha_u8 = np.zeros((30, 40), dtype=np.uint8)
    cv2.putText(alpha_u8, "APP", (2, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.65, 255, 2)
    alpha = alpha_u8.astype(np.float32) / 255.0 * 0.35
    marked = _apply_overlay(clean, alpha, (10, 35), (220, 220, 220))

    restored, mask = reverse_alpha_blend(
        marked,
        alpha,
        position=(10, 35),
        foreground_bgr=(220, 220, 220),
    )

    active = mask > 0
    assert np.max(np.abs(restored[active].astype(int) - clean[active].astype(int))) <= 2
    assert np.array_equal(restored[~active], marked[~active])


def test_estimate_alpha_map_recovers_calibration_pair() -> None:
    clean = np.full((30, 40, 3), (40, 80, 120), dtype=np.uint8)
    expected = np.zeros((30, 40), dtype=np.float32)
    expected[5:25, 8:32] = 0.25
    marked = _apply_overlay(clean, expected, (0, 0), (240, 240, 240))

    estimated = estimate_alpha_map(
        clean, marked, foreground_bgr=(240, 240, 240)
    )

    assert np.max(np.abs(estimated[5:25, 8:32] - 0.25)) < 0.02
    assert np.count_nonzero(estimated[:5]) == 0


def test_text_candidate_mask_finds_three_low_saturation_lines() -> None:
    roi = np.full((230, 520, 3), (80, 105, 135), dtype=np.uint8)
    for text, origin in (("APP", (12, 70)), ("ADDRESS", (12, 145)), ("2026", (12, 215))):
        cv2.putText(
            roi,
            text,
            origin,
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (185, 185, 185),
            2,
            cv2.LINE_AA,
        )

    mask, confidence = build_text_candidate_mask(roi)

    assert np.count_nonzero(mask) > 0
    assert confidence >= 0.38


def test_ocr_polygons_are_refined_to_strokes_not_full_rectangles() -> None:
    roi = np.full((230, 520, 3), (80, 105, 135), dtype=np.uint8)
    cv2.putText(
        roi,
        "ADDRESS",
        (20, 145),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.2,
        (190, 190, 190),
        2,
        cv2.LINE_AA,
    )
    polygon = np.asarray([[10, 95], [300, 95], [300, 160], [10, 160]], dtype=np.float32)

    mask, confidence = build_ocr_text_mask(roi, [polygon])

    rectangle_area = (300 - 10) * (160 - 95)
    assert 0 < np.count_nonzero(mask) < rectangle_area * 0.65
    assert confidence > 0


def test_ocr_stroke_refinement_scales_for_high_resolution_punctuation() -> None:
    def make_mask(height: int, width: int) -> np.ndarray:
        roi = np.full((height, width, 3), (80, 105, 135), dtype=np.uint8)
        scale = height / 230
        cv2.putText(
            roi,
            "******",
            (round(20 * scale), round(145 * scale)),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2 * scale,
            (190, 190, 190),
            max(2, round(2 * scale)),
            cv2.LINE_AA,
        )
        polygon = np.asarray(
            [
                [round(10 * scale), round(95 * scale)],
                [round(360 * scale), round(95 * scale)],
                [round(360 * scale), round(160 * scale)],
                [round(10 * scale), round(160 * scale)],
            ],
            dtype=np.float32,
        )
        mask, _ = build_ocr_text_mask(roi, [polygon])
        return mask

    reference = make_mask(230, 520)
    high_resolution = make_mask(640, 1440)

    reference_ratio = np.count_nonzero(reference) / reference.size
    high_resolution_ratio = np.count_nonzero(high_resolution) / high_resolution.size
    assert high_resolution_ratio >= reference_ratio * 0.85


def test_ocr_address_line_captures_trailing_symbols_outside_detected_box() -> None:
    roi = np.full((640, 1440, 3), (80, 105, 135), dtype=np.uint8)
    cv2.putText(
        roi,
        "ADDRESS",
        (45, 400),
        cv2.FONT_HERSHEY_SIMPLEX,
        3.0,
        (190, 190, 190),
        6,
        cv2.LINE_AA,
    )
    cv2.putText(
        roi,
        "*****",
        (1100, 400),
        cv2.FONT_HERSHEY_SIMPLEX,
        3.0,
        (190, 190, 190),
        6,
        cv2.LINE_AA,
    )
    shortened_polygon = np.asarray(
        [[45, 284], [1084, 287], [1083, 440], [45, 437]], dtype=np.float32
    )

    mask, _ = build_ocr_text_mask(roi, [shortened_polygon])

    assert np.count_nonzero(mask[300:450, 1100:1295]) > 0


def test_dangjia_fallback_never_changes_pixels_outside_roi(tmp_path: Path) -> None:
    image = np.full((400, 300, 3), (80, 105, 135), dtype=np.uint8)
    for text, origin in (("APP", (8, 345)), ("ADDR", (8, 370)), ("2026", (8, 395))):
        cv2.putText(
            image,
            text,
            origin,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (190, 190, 190),
            2,
            cv2.LINE_AA,
        )
    before = image.copy()
    processor = DangjiaWatermarkProcessor(tmp_path / "missing-profile")

    result = processor.remove(
        image,
        WatermarkRemovalConfig(
            enabled=True,
            roi=(0, 0.8, 0.55, 1),
            detection_threshold=0,
            roi_ocr_enabled=False,
        ),
    )

    assert result.audit["status"] == "applied"
    assert result.audit["outside_roi_changed_pixels"] == 0
    assert np.array_equal(result.image_bgr[:320], before[:320])
