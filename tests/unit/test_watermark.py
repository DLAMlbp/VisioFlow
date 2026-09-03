from pathlib import Path

import cv2
import numpy as np

from src.services.images.watermark import (
    DangjiaWatermarkProcessor,
    app_token_box_from_text_line,
    build_ocr_text_mask,
    build_text_candidate_mask,
    detect_watermark_app_boxes,
    estimate_alpha_map,
    reverse_alpha_blend,
)
from src.services.profiles import WatermarkRemovalConfig
from src.services.images.adapters.rapidocr import OcrTextLine


def test_app_token_box_projects_only_app_inside_an_ocr_line() -> None:
    polygon = np.asarray(
        [[10, 20], [220, 20], [220, 60], [10, 60]], dtype=np.float32
    )

    box = app_token_box_from_text_line(polygon, "当家APP拍摄")

    assert box is not None
    assert 60 <= box[0] <= 75
    assert 155 <= box[2] <= 170
    assert box[1] < 20
    assert box[3] > 60
    assert app_token_box_from_text_line(polygon, "APPLICATION") is None


def test_watermark_app_detection_is_limited_to_configured_roi(monkeypatch) -> None:
    image = np.zeros((400, 300, 3), dtype=np.uint8)
    received_shape = None

    def fake_lines(roi):
        nonlocal received_shape
        received_shape = roi.shape
        return [
            OcrTextLine(
                polygon=np.asarray(
                    [[10, 20], [150, 20], [150, 50], [10, 50]], dtype=np.float32
                ),
                text="当家APP拍摄",
                confidence=0.95,
            )
        ]

    monkeypatch.setattr("src.services.images.watermark.detect_text_lines", fake_lines)

    boxes, confidences, roi_box = detect_watermark_app_boxes(
        image,
        WatermarkRemovalConfig(
            enabled=True,
            roi=(0, 0.8, 0.55, 1),
            detection_threshold=0.8,
        ),
    )

    assert received_shape == (80, 165, 3)
    assert roi_box == (0, 320, 165, 400)
    assert confidences == [0.95]
    assert len(boxes) == 1
    assert boxes[0][1] >= 320


def test_app_overlay_preparation_never_requests_inpainting(
    tmp_path: Path, monkeypatch
) -> None:
    image = np.zeros((400, 300, 3), dtype=np.uint8)
    monkeypatch.setattr(
        "src.services.images.watermark.detect_watermark_app_boxes",
        lambda *_args: ([(12, 340, 48, 365)], [0.97], (0, 320, 165, 400)),
    )

    preparation = DangjiaWatermarkProcessor(
        tmp_path / "missing-profile"
    ).prepare_app_overlay(image, WatermarkRemovalConfig(enabled=True))

    assert preparation.requires_inpaint is False
    assert np.count_nonzero(preparation.mask) == 0
    assert preparation.audit["status"] == "detected"
    assert preparation.audit["automatic_boxes"] == [[12, 340, 48, 365]]
    assert np.array_equal(preparation.image_bgr, image)


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


def test_watermark_detection_does_not_load_or_run_lama(
    tmp_path: Path, monkeypatch
) -> None:
    image = np.full((400, 300, 3), (80, 105, 135), dtype=np.uint8)
    cv2.putText(
        image,
        "APP ADDRESS 2026",
        (5, 380),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (190, 190, 190),
        2,
        cv2.LINE_AA,
    )
    called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("LaMa must not run during watermark detection")

    def detected_mask(roi):
        mask = np.zeros(roi.shape[:2], dtype=np.uint8)
        mask[:, :10] = 255
        return mask, 0.9

    monkeypatch.setattr(
        "src.services.images.watermark.erase_lama_opencv",
        fail_if_called,
    )
    monkeypatch.setattr(
        "src.services.images.watermark.build_text_candidate_mask",
        detected_mask,
    )
    processor = DangjiaWatermarkProcessor(tmp_path / "missing-profile")

    preparation = processor.prepare(
        image,
        WatermarkRemovalConfig(
            enabled=True,
            roi=(0, 0.8, 0.55, 1),
            detection_threshold=0.38,
            roi_ocr_enabled=False,
        ),
    )

    assert preparation.requires_inpaint is True
    assert preparation.audit["status"] == "detected"
    assert np.count_nonzero(preparation.mask) > 0
    assert called is False
