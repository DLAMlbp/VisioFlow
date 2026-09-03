from __future__ import annotations

import numpy as np

from src.services.images.logo_overlay import apply_logo_overlays, load_overlay_asset
from src.services.images.processing_vision import (
    BrandedGroundFilmAssessment,
    RedactionAssessment,
)
from src.services.images.redaction import _app_token_box
from src.services.images.redaction_policy import evaluate_ground_film
from src.services.managed_profiles import _compile_redaction_instruction
from src.services.profiles import LogoMosaicConfig, RedactionProfile

INSTRUCTION = """图片左下角水印允许通过筛选，通过后自动去除。
保留当家文字，仅使用小当图标遮挡APP。
当家APP品牌地膜占整张图片大于等于75%时判定不合格。"""


def _profile() -> RedactionProfile:
    compiled = _compile_redaction_instruction(INSTRUCTION)
    return RedactionProfile.model_validate(compiled.config)


def _assessment(*, coverage: float, confidence: float = 0.95) -> RedactionAssessment:
    return RedactionAssessment(
        left_bottom_watermark_detected=False,
        target_logo_detected=True,
        branded_ground_film=BrandedGroundFilmAssessment(
            detected=True,
            brand_detected=True,
            coverage_ratio=coverage,
            confidence=confidence,
            reason="画面主要为印有当家APP的地面保护膜",
        ),
    )


def test_compiles_confirmed_redaction_requirement() -> None:
    compiled = _compile_redaction_instruction(INSTRUCTION)
    profile = RedactionProfile.model_validate(compiled.config)

    assert compiled.unsupported == []
    assert profile.watermark.allow_during_filter is True
    assert profile.watermark.post_action == "remove"
    assert profile.logo.action == "overlay_asset"
    assert profile.logo.target_component == "app_text"
    assert profile.logo.overlay_asset_id == "xiaodang_cutout_v1"
    assert profile.branded_ground_film.reject_coverage_gte == 0.75


def test_legacy_overlay_profiles_default_to_app_only() -> None:
    assert LogoMosaicConfig(action="overlay_asset").target_component == "app_text"


def test_ground_film_threshold_is_inclusive_and_review_band_is_safe() -> None:
    profile = _profile()

    assert evaluate_ground_film(profile, _assessment(coverage=0.75)).rejected is True
    near = evaluate_ground_film(profile, _assessment(coverage=0.72))
    assert near.rejected is False
    assert near.review_required is True
    assert evaluate_ground_film(profile, _assessment(coverage=0.60)).review_required is False


def test_low_confidence_ground_film_requires_review_instead_of_rejection() -> None:
    decision = evaluate_ground_film(_profile(), _assessment(coverage=0.90, confidence=0.5))

    assert decision.rejected is False
    assert decision.review_required is True


def test_xiaodang_overlay_is_transparent_and_covers_target() -> None:
    asset, digest = load_overlay_asset("xiaodang_cutout_v1")
    image = np.full((240, 320, 3), (90, 120, 150), dtype=np.uint8)
    result, boxes, returned_digest = apply_logo_overlays(
        image,
        [(100, 100, 180, 130)],
        asset_id="xiaodang_cutout_v1",
        expansion=0,
        scale=1.12,
    )

    assert asset.shape[2] == 4
    assert np.any(asset[:, :, 3] == 0)
    assert len(digest) == 64
    assert returned_digest == digest
    assert boxes
    assert np.any(result != image)
    x0, y0, _, _ = boxes[0]
    assert np.array_equal(result[y0, x0], image[y0, x0])
    assert np.array_equal(result[:, :100], image[:, :100])


def test_app_token_box_keeps_dangjia_side_outside_the_cover_target() -> None:
    assert _app_token_box((100, 40, 300, 100)) == (210, 40, 300, 100)
