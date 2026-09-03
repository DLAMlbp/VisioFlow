from io import BytesIO

from PIL import Image, ImageFilter
import pytest

from src.models.library_asset import LibraryAsset
from src.models.library_asset_group import LibraryAssetGroup
from src.services.images.similarity import (
    ScoredCandidate,
    apply_shadow_mode,
    combined_similarity_score,
    decide_similarity,
    feature_similarity,
    feature_similarity_evidence,
    reliability_weighted_similarity_score,
)
from src.services.profiles import SimilarityProfile
from src.workers.library import _prepare_library_image


def test_library_image_preparation_does_not_apply_quality_filtering() -> None:
    image = Image.new("RGB", (48, 24), (3, 3, 3)).filter(ImageFilter.GaussianBlur(radius=12))
    output = BytesIO()
    image.save(output, format="JPEG")
    prepared = _prepare_library_image(output.getvalue())
    assert prepared.width == 48
    assert prepared.normalized_bytes.startswith(b"\xff\xd8\xff")


def test_hybrid_match_combines_image_and_content_scores() -> None:
    result = decide_similarity(candidates=[
        _candidate("ast_1", ["完工", "厨房"], 0.67, 0.80),
        _candidate("ast_2", ["完工", "客厅"], 0.62, 0.45)], settings=_policy())
    assert result.decision == "matched"
    assert result.tags == ["完工", "厨房"]
    assert result.final_score == 0.709
    assert result.feature_score == 0.80


def test_final_score_above_sixty_matches_even_when_candidates_are_close() -> None:
    result = decide_similarity(candidates=[
        _candidate("ast_1", ["完工", "厨房"], 0.90, 0.90),
        _candidate("ast_2", ["完工", "客厅"], 0.88, 0.88)],
        settings=_policy().model_copy(update={"similarity_min_margin": 0.05}))
    assert result.decision == "matched"
    assert result.tags == ["完工", "厨房"]
    assert result.candidates[0]["tags"] == ["完工", "厨房"]
    assert result.candidates[0]["preview_object_key"] == "uploads/ast_1.jpg"


def test_same_reference_image_prefers_the_broader_tag_set() -> None:
    result = decide_similarity(
        candidates=[
            _candidate("ast_narrow", ["日常", "新房"], 0.91, 0.90, sha256="same-image"),
            _candidate(
                "ast_broad",
                ["日常", "新房", "精装房", "旧房", "改建"],
                0.90,
                0.89,
                sha256="same-image",
            ),
        ],
        settings=_policy().model_copy(update={"similarity_min_margin": 0.05}),
    )

    assert result.decision == "matched"
    assert result.matched_asset_id == "ast_broad"
    assert result.tags == ["日常", "新房", "精装房", "旧房", "改建"]
    assert "标签范围更广" in result.message


def test_missing_content_features_fall_back_to_image_score() -> None:
    result = decide_similarity(
        candidates=[_candidate("ast_1", ["施工", "水电"], 0.78, None)],
        settings=_policy(),
    )
    assert result.decision == "matched"
    assert result.tags == ["施工", "水电"]
    assert result.feature_score is None
    assert result.final_score == 0.78


def test_low_content_score_prevents_automatic_match() -> None:
    result = decide_similarity(
        candidates=[_candidate("ast_1", ["施工", "水电"], 0.80, 0.10)],
        settings=_policy(),
    )
    assert result.decision == "pending_review"
    assert result.final_score == 0.59


def test_below_review_threshold_is_unmatched() -> None:
    result = decide_similarity(
        candidates=[_candidate("ast_1", ["施工", "水电"], 0.40, 0.20)],
        settings=_policy(),
    )
    assert result.decision == "unmatched"
    assert result.tags == []
    assert result.matched_asset_id is None


def test_shadow_mode_converts_automatic_match_to_review_without_tags() -> None:
    automatic = decide_similarity(
        candidates=[
            _candidate("ast_1", ["完工", "厨房"], 0.91, 0.90),
            _candidate("ast_2", ["完工", "客厅"], 0.80, 0.70),
        ],
        settings=_policy(),
    )

    result = apply_shadow_mode(automatic, enabled=True)

    assert result.decision == "pending_review"
    assert result.tags == []
    assert result.matched_asset_id == "ast_1"
    assert result.similarity_score == 0.91
    assert result.feature_score == 0.90
    assert "Shadow" in result.message


def test_shadow_mode_does_not_change_review_or_unmatched_decisions() -> None:
    unmatched = decide_similarity(
        candidates=[_candidate("ast_1", ["施工", "水电"], 0.40, None)],
        settings=_policy(),
    )

    assert apply_shadow_mode(unmatched, enabled=True) is unmatched
    assert apply_shadow_mode(unmatched, enabled=False) is unmatched


def test_structured_content_feature_similarity() -> None:
    score = feature_similarity(
        {
            "scene": "住宅室内",
            "space": "厨房",
            "condition": "已完工",
            "objects": ["橱柜", "吊灯"],
        },
        {
            "scene": "住宅室内",
            "space": "厨房",
            "condition": "完工",
            "objects": ["橱柜", "餐桌"],
        },
    )
    assert score is not None
    assert score > 0.60


def test_missing_fields_are_excluded_and_weights_are_renormalized() -> None:
    evidence = feature_similarity_evidence(
        {"scene": "住宅室内", "space": "厨房"},
        {"scene": "住宅室内"},
    )

    assert evidence.score == 1.0
    assert evidence.reliability == 0.7
    assert evidence.coverage == 0.15
    assert set(evidence.field_scores) == {"scene"}


def test_semantically_related_long_descriptions_are_not_false_conflicts() -> None:
    evidence = feature_similarity_evidence(
        {"scene": "室内装修开工庆祝活动现场", "confidence": 0.9},
        {"scene": "室内庆祝活动现场，三人站在红色背景前", "confidence": 0.9},
    )

    assert evidence.score is not None
    assert evidence.score > 0.0
    assert evidence.field_scores["scene"]["method"] == "canonical"


def test_inconclusive_free_text_is_audited_but_does_not_change_score() -> None:
    evidence = feature_similarity_evidence(
        {"scene": "红色背景前多人合影", "confidence": 0.95},
        {"scene": "装修公司开工大吉", "confidence": 0.95},
    )

    assert evidence.score is None
    assert evidence.reliability == 0.0
    assert evidence.coverage == 0.15
    assert evidence.field_scores["scene"]["method"] == "inconclusive"

    result = reliability_weighted_similarity_score(
        similarity_score=0.6632,
        feature_score=evidence.score,
        feature_reliability=evidence.reliability,
        feature_coverage=evidence.coverage,
    )
    assert result.final_score == 0.6632


def test_only_canonical_mutually_exclusive_values_are_hard_conflicts() -> None:
    evidence = feature_similarity_evidence(
        {"condition": "施工中", "confidence": 0.95},
        {"condition": "已经完工", "confidence": 0.95},
    )

    assert evidence.score == 0.0
    assert evidence.field_scores["condition"]["method"] == "canonical_conflict"


def test_low_coverage_content_does_not_over_penalize_image_score() -> None:
    result = reliability_weighted_similarity_score(
        similarity_score=0.6632,
        feature_score=0.4379,
        feature_reliability=0.7,
        feature_coverage=0.15,
    )

    assert result.feature_weight == pytest.approx(0.0315)
    assert result.final_score > 0.60


def test_high_reliability_conflict_can_block_an_automatic_match() -> None:
    result = reliability_weighted_similarity_score(
        similarity_score=0.80,
        feature_score=0.10,
        feature_reliability=1.0,
    )

    assert result.feature_weight == pytest.approx(0.30)
    assert result.final_score < 0.60


def test_similarity_score_is_always_clamped_to_valid_range() -> None:
    high = reliability_weighted_similarity_score(
        similarity_score=2.0,
        feature_score=3.0,
        feature_reliability=4.0,
    )
    low = reliability_weighted_similarity_score(
        similarity_score=-2.0,
        feature_score=-3.0,
        feature_reliability=-4.0,
    )

    assert high.final_score == 1.0
    assert low.final_score == 0.0


def test_candidate_margin_is_audit_only_and_does_not_veto_sixty_points() -> None:
    result = decide_similarity(
        candidates=[
            _candidate("ast_1", ["厨房"], 0.80, 0.80),
            _candidate("ast_2", ["客厅"], 0.76, 0.76),
        ],
        settings=_policy().model_copy(update={"similarity_min_margin": 0.05}),
    )

    assert result.decision == "matched"
    assert round(result.candidate_margin or 0, 6) == 0.04
    assert "candidate_margin" not in result.candidates[0]


def test_same_candidates_produce_identical_result_twenty_times() -> None:
    candidates = [
        _candidate("ast_1", ["开工大吉"], 0.6632, 0.72),
        _candidate("ast_2", ["完工"], 0.57, 0.80),
    ]

    results = [
        decide_similarity(candidates=candidates, settings=_policy())
        for _ in range(20)
    ]

    assert {result.matched_asset_id for result in results} == {"ast_1"}
    assert {result.final_score for result in results} == {results[0].final_score}
    assert {result.decision for result in results} == {"matched"}


def _candidate(
    asset_id: str,
    tags: list[str],
    image_score: float,
    content_score: float | None,
    sha256: str | None = None,
) -> ScoredCandidate:
    group = LibraryAssetGroup(id=f"grp_{asset_id}", tags=tags, tag_key="\x1f".join(tags), status="active")
    asset = LibraryAsset(id=asset_id, original_object_key=f"uploads/{asset_id}.jpg",
        group_id=group.id, group=group, status="active", sha256=sha256)
    policy = _policy()
    return ScoredCandidate(
        asset=asset,
        tags=tags,
        similarity_score=image_score,
        feature_score=content_score,
        final_score=combined_similarity_score(
            similarity_score=image_score,
            feature_score=content_score,
            settings=policy,
        ),
    )


def _policy() -> SimilarityProfile:
    return SimilarityProfile.model_validate({
        "id": "library_similarity_v2", "version": 9, "description": "hybrid",
        "similarity_candidate_limit": 20, "similarity_image_weight": 0.70,
        "similarity_feature_weight": 0.30, "similarity_auto_threshold": 0.60,
        "similarity_dynamic_weighting_enabled": True,
        "similarity_review_threshold": 0.45, "similarity_min_margin": 0.00})
