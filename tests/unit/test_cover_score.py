import pytest

from src.services.images.cover_score import (
    CoverAssessment,
    calculate_cover_score,
    cover_score_from_processing,
)


def _assessment(
    *,
    scene_completeness: int = 5,
    composition: int = 5,
    visual_appeal: int = 5,
    representativeness: int = 5,
    hard_fail: bool = False,
) -> CoverAssessment:
    return CoverAssessment(
        scene_completeness=scene_completeness,
        composition=composition,
        visual_appeal=visual_appeal,
        representativeness=representativeness,
        hard_fail=hard_fail,
        risk_codes=["incomplete_scene"] if hard_fail else [],
    )


def test_95_requires_four_perfect_semantic_ratings_and_technical_floor() -> None:
    assessment = _assessment()

    assert calculate_cover_score(assessment, technical_score=80) == 95.0
    assert calculate_cover_score(assessment, technical_score=100) == 100.0
    assert calculate_cover_score(assessment, technical_score=79.99) == 94.99


@pytest.mark.parametrize(
    "field",
    ["scene_completeness", "composition", "visual_appeal", "representativeness"],
)
def test_any_semantic_rating_of_four_caps_score_below_95(field: str) -> None:
    assessment = _assessment(**{field: 4})

    assert calculate_cover_score(assessment, technical_score=100) == 94.99


def test_ordinary_and_hard_fail_images_are_strictly_gated() -> None:
    assert calculate_cover_score(_assessment(composition=3), technical_score=100) == 89.99
    assert calculate_cover_score(_assessment(composition=2), technical_score=100) == 79.99
    assert calculate_cover_score(_assessment(hard_fail=True), technical_score=100) == 79.99


def test_enhanced_technical_metrics_recalculate_the_cover_score() -> None:
    processing = {"cover_assessment": _assessment().model_dump(mode="json")}

    preliminary = cover_score_from_processing(processing, technical_score=80)
    enhanced = cover_score_from_processing(processing, technical_score=92)

    assert preliminary == 95.0
    assert enhanced == 98.0


@pytest.mark.parametrize(
    "processing",
    [None, {}, {"cover_assessment": {"composition": "invalid"}}],
)
def test_legacy_or_invalid_payload_preserves_previous_score(processing: object) -> None:
    assert (
        cover_score_from_processing(
            processing,
            technical_score=100,
            legacy_fallback_score=87.65,
        )
        == 87.65
    )
