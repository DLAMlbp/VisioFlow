import numpy as np

from src.services.images.logo_detector import LogoDetection
from src.services.images.redaction import (
    ImageRedactionService,
    ImageStageResult,
    boxes_area_ratio,
    changed_pixels_outside_box,
    changed_pixels_outside_boxes,
    normalized_roi_to_box,
)
from src.services.profiles import LogoMosaicConfig, WatermarkRemovalConfig


class _WatermarkProcessor:
    profile_version = "test-watermark-v1"

    def remove(self, image_bgr, config):
        result = image_bgr.copy()
        box = normalized_roi_to_box(
            config.roi,
            image_width=image_bgr.shape[1],
            image_height=image_bgr.shape[0],
        )
        x0, y0, x1, y1 = box
        result[y0:y1, x0:x1] = 0
        return ImageStageResult(
            result,
            {
                "status": "applied",
                "roi_px": list(box),
                "outside_roi_changed_pixels": changed_pixels_outside_box(
                    image_bgr, result, box
                ),
            },
        )


class _LogoDetector:
    model_version = "test-logo-v1"

    def detect(self, image_bgr, config):
        del image_bgr, config
        return [
            LogoDetection((20, 20, 50, 45), 0.9),
            LogoDetection((70, 10, 90, 30), 0.2),
            LogoDetection((5, 5, 15, 15), 0.95, product_logo=True),
        ]


def test_normalized_roi_matches_the_reference_portrait() -> None:
    assert normalized_roi_to_box(
        (0, 0.84, 0.48, 1), image_width=1080, image_height=1440
    ) == (0, 1210, 518, 1440)


def test_disabled_redaction_is_a_pixel_exact_noop() -> None:
    image = np.full((60, 80, 3), 99, dtype=np.uint8)
    service = ImageRedactionService()

    watermark = service.remove_watermark(image, WatermarkRemovalConfig())
    logos = service.mosaic_logos(image, LogoMosaicConfig())

    assert watermark.audit == {"enabled": False, "status": "disabled"}
    assert logos.audit == {"enabled": False, "status": "disabled"}
    assert np.array_equal(watermark.image_bgr, image)
    assert np.array_equal(logos.image_bgr, image)


def test_watermark_processor_is_confined_to_its_roi() -> None:
    image = np.full((100, 120, 3), 255, dtype=np.uint8)
    service = ImageRedactionService(watermark_processor=_WatermarkProcessor())

    result = service.remove_watermark(
        image,
        WatermarkRemovalConfig(enabled=True, roi=(0, 0.8, 0.5, 1)),
    )

    assert result.audit["status"] == "applied"
    assert result.audit["outside_roi_changed_pixels"] == 0
    assert np.array_equal(result.image_bgr[:80], image[:80])


def test_logo_mosaic_filters_confidence_and_product_logos() -> None:
    image = np.zeros((80, 100, 3), dtype=np.uint8)
    for x in range(100):
        image[:, x] = (x, 255 - x, (x * 9) % 255)
    service = ImageRedactionService(logo_detector=_LogoDetector())

    result = service.mosaic_logos(
        image,
        LogoMosaicConfig(enabled=True, confidence=0.45, include_product_logos=False),
    )

    assert result.audit["status"] == "applied"
    assert result.audit["detections"] == 1
    assert result.audit["model_version"] == "test-logo-v1"
    assert result.audit["outside_boxes_changed_pixels"] == 0
    assert result.audit["modified_area_ratio"] > 0


def test_logo_change_audit_detects_pixels_outside_boxes() -> None:
    before = np.zeros((20, 30, 3), dtype=np.uint8)
    after = before.copy()
    after[5:10, 5:10] = 255
    after[0, 0] = 255
    boxes = [(5, 5, 10, 10)]

    assert changed_pixels_outside_boxes(before, after, boxes) == 1
    assert boxes_area_ratio(before.shape, boxes) == 25 / 600


def test_redaction_failure_keeps_the_input() -> None:
    image = np.full((50, 50, 3), 127, dtype=np.uint8)
    service = ImageRedactionService()

    result = service.remove_watermark(image, WatermarkRemovalConfig(enabled=True))

    assert result.audit["status"] == "failed_safe"
    assert np.array_equal(result.image_bgr, image)
