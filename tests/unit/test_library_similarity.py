from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image, ImageFilter

from src.models.library_asset import LibraryAsset
from src.models.library_asset_group import LibraryAssetGroup
from src.services.images.similarity import (
    CORE_MODE_FALLBACK,
    CORE_MODE_SUPPORTED,
    CORE_MODE_UNSUPPORTED,
    ScoredCandidate,
    combined_similarity_score,
    decide_similarity,
    feature_similarity,
    feature_similarity_evidence,
    reliability_weighted_similarity_score,
    score_candidate,
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


def test_close_candidate_groups_adopt_highest_score_above_threshold() -> None:
    result = decide_similarity(candidates=[
        _candidate("ast_1", ["完工", "厨房"], 0.90, 0.90),
        _candidate("ast_2", ["完工", "客厅"], 0.88, 0.88)],
        settings=_policy().model_copy(update={"similarity_min_margin": 0.05}))
    assert result.decision == "matched"
    assert result.tags == ["完工", "厨房"]
    assert result.matched_asset_id == "ast_1"
    assert result.candidates[0]["tags"] == ["完工", "厨房"]
    assert result.candidates[0]["preview_object_key"] == "uploads/ast_1.jpg"


def test_same_reference_image_in_different_groups_adopts_highest_score() -> None:
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
    assert result.tags == ["日常", "新房"]
    assert result.candidate_margin == pytest.approx(0.01)


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
    assert result.decision == "unmatched"
    assert result.final_score == 0.59


def test_below_auto_threshold_is_unmatched() -> None:
    result = decide_similarity(
        candidates=[_candidate("ast_1", ["施工", "水电"], 0.40, 0.20)],
        settings=_policy(),
    )
    assert result.decision == "unmatched"
    assert result.tags == []
    assert result.matched_asset_id is None


@pytest.mark.parametrize(
    ("final_score", "feature_score", "expected_decision"),
    [
        (0.60, 0.10, "matched"),
        (0.5999, 0.7501, "matched"),
        (0.5999, 0.75, "matched"),
        (0.5999, None, "unmatched"),
    ],
)
def test_adoption_uses_final_or_feature_score_threshold(
    final_score: float,
    feature_score: float | None,
    expected_decision: str,
) -> None:
    candidate = replace(
        _candidate("ast_1", ["施工", "水电"], 0.10, feature_score),
        final_score=final_score,
    )

    result = decide_similarity(candidates=[candidate], settings=_policy())

    assert result.decision == expected_decision
    assert result.tags == (["施工", "水电"] if expected_decision == "matched" else [])


@pytest.mark.parametrize(
    ("score", "expected_decision", "expected_tags"),
    [
        (0.70, "matched", ["施工", "水电"]),
        (0.6999, "unmatched", []),
    ],
)
def test_binary_threshold_includes_seventy_percent_and_rejects_below(
    score: float,
    expected_decision: str,
    expected_tags: list[str],
) -> None:
    policy = _policy().model_copy(
        update={
            "similarity_auto_threshold": 0.70,
            "similarity_review_threshold": 0.70,
            "similarity_min_margin": 0.0,
        }
    )

    result = decide_similarity(
        candidates=[_candidate("ast_1", ["施工", "水电"], score, None)],
        settings=policy,
    )

    assert result.decision == expected_decision
    assert result.tags == expected_tags


def test_unsupported_candidate_matches_at_seventy_with_three_percent_margin() -> None:
    best = _candidate("mudwork", ["泥工验收"], 0.7257, None)
    best = ScoredCandidate(
        **{
            **best.__dict__,
            "core_evidence_mode": CORE_MODE_UNSUPPORTED,
            "core_requirements_passed": False,
        }
    )
    second = _candidate("water", ["水电验收"], 0.6847, None)
    second = ScoredCandidate(
        **{
            **second.__dict__,
            "core_evidence_mode": CORE_MODE_UNSUPPORTED,
            "core_requirements_passed": False,
        }
    )
    policy = _policy().model_copy(
        update={
            "similarity_auto_threshold": 0.70,
            "similarity_review_threshold": 0.70,
            "similarity_min_margin": 0.03,
            "similarity_group_unsupported_auto_threshold": 0.70,
        }
    )

    result = decide_similarity(candidates=[best, second], settings=policy)

    assert result.decision == "matched"
    assert result.tags == ["泥工验收"]
    assert result.candidate_margin == pytest.approx(0.041)


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


def test_candidate_margin_is_diagnostic_and_cannot_veto_match() -> None:
    result = decide_similarity(
        candidates=[
            _candidate("ast_1", ["厨房"], 0.80, 0.80),
            _candidate("ast_2", ["客厅"], 0.76, 0.76),
        ],
        settings=_policy().model_copy(update={"similarity_min_margin": 0.05}),
    )

    assert result.decision == "matched"
    assert result.tags == ["厨房"]
    assert round(result.candidate_margin or 0, 6) == 0.04
    assert "candidate_margin" not in result.candidates[0]


def test_assets_in_same_group_do_not_reduce_group_margin() -> None:
    shared_group = LibraryAssetGroup(
        id="grp_shared",
        tags=["日常", "拆除"],
        tag_key="日常\x1f拆除",
        status="active",
    )
    first = _candidate("ast_1", shared_group.tags, 0.90, 0.90, group=shared_group)
    second = _candidate("ast_2", shared_group.tags, 0.89, 0.89, group=shared_group)
    other = _candidate("ast_3", ["日常", "水电"], 0.70, 0.70)

    result = decide_similarity(
        candidates=[first, second, other],
        settings=_policy().model_copy(update={"similarity_min_margin": 0.05}),
    )

    assert result.decision == "matched"
    assert result.matched_asset_id == "ast_1"
    assert result.candidate_margin == pytest.approx(0.20)
    assert [candidate["group_id"] for candidate in result.candidates] == [
        "grp_shared",
        "grp_ast_3",
    ]


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


def test_specific_group_adoption_uses_final_score_without_extra_evidence_gate() -> None:
    policy = _policy().model_copy(
        update={
            "similarity_group_matching_enabled": True,
            "similarity_group_tag_rules": {
                "卫生间": {
                    "dimension": "space",
                    "fields": ["space", "scene", "subjects", "objects"],
                    "keywords": ["卫生间", "浴室", "马桶"],
                }
            },
        }
    )
    group = LibraryAssetGroup(
        id="grp_bathroom",
        tags=["日常", "旧房", "拆除", "卫生间"],
        tag_key="日常\x1f旧房\x1f拆除\x1f卫生间",
        status="active",
    )
    asset = LibraryAsset(
        id="ast_bathroom",
        original_object_key="uploads/bathroom.jpg",
        group_id=group.id,
        group=group,
        status="active",
        analysis_json={"space": "卫生间", "content_confidence": 0.95},
    )

    candidate = score_candidate(
        asset=asset,
        tags=list(group.tags),
        similarity_score=0.90,
        query_content={
            "space": "墙体转角及砖砌结构区域",
            "scene": "建筑施工中的墙体局部",
            "objects": ["红色砖块", "砂浆", "管线"],
            # Stale output fields must never count as visual evidence.
            "tags": ["卫生间"],
            "categories": {"素材库标签": ["卫生间"]},
            "content_confidence": 0.93,
        },
        settings=policy,
    )
    result = decide_similarity(candidates=[candidate], settings=policy)

    assert candidate.core_requirements_passed is False
    assert result.decision == "matched"
    assert result.tags == group.tags


def test_specific_group_keeps_complete_tag_set_when_core_evidence_passes() -> None:
    policy = _policy().model_copy(
        update={
            "similarity_group_matching_enabled": True,
            "similarity_group_tag_rules": {
                "卫生间": {
                    "dimension": "space",
                    "fields": ["space", "scene", "subjects", "objects"],
                    "keywords": ["卫生间", "浴室", "马桶"],
                }
            },
        }
    )
    tags = ["日常", "施工报价", "旧房", "局改", "拆除", "卫生间"]
    group = LibraryAssetGroup(
        id="grp_bathroom",
        tags=tags,
        tag_key="\x1f".join(tags),
        status="active",
    )
    asset = LibraryAsset(
        id="ast_bathroom",
        original_object_key="uploads/bathroom.jpg",
        group_id=group.id,
        group=group,
        status="active",
        analysis_json={"space": "卫生间", "content_confidence": 0.95},
    )

    candidate = score_candidate(
        asset=asset,
        tags=tags,
        similarity_score=0.90,
        query_content={
            "space": "正在拆除的卫生间",
            "objects": ["马桶", "墙砖"],
            "content_confidence": 0.95,
        },
        settings=policy,
    )
    result = decide_similarity(candidates=[candidate], settings=policy)

    assert candidate.core_requirements_passed is True
    assert result.decision == "matched"
    assert result.tags == tags


def test_canonical_concepts_support_non_literal_construction_stage_evidence() -> None:
    policy = _policy().model_copy(
        update={
            "similarity_group_matching_enabled": True,
            "similarity_semantic_concepts": {
                "woodwork": ["石膏板", "龙骨", "吊顶"],
                "completed_state": ["封板完成", "已成型"],
            },
            "similarity_group_tag_rules": {
                "木工完工": {
                    "dimension": "stage",
                    "fields": ["scene", "condition", "objects", "features"],
                    "keywords": ["木工完工"],
                    "required_concepts": ["woodwork"],
                    "supporting_concepts": ["completed_state"],
                }
            },
        }
    )
    tags = ["日常", "新房", "木工完工"]
    group = LibraryAssetGroup(
        id="grp_woodwork",
        tags=tags,
        tag_key="\x1f".join(tags),
        status="active",
    )
    asset = LibraryAsset(
        id="ast_woodwork",
        original_object_key="uploads/woodwork.jpg",
        group_id=group.id,
        group=group,
        status="active",
        analysis_json={"scene": "室内施工", "objects": ["石膏板", "轻钢龙骨"]},
    )

    candidate = score_candidate(
        asset=asset,
        tags=tags,
        similarity_score=0.86,
        query_content={
            "scene": "顶面造型施工现场",
            "condition": "吊顶封板完成，整体已成型",
            "objects": ["绿色石膏板", "龙骨"],
            "content_confidence": 0.94,
        },
        settings=policy,
    )
    result = decide_similarity(candidates=[candidate], settings=policy)

    assert candidate.core_requirements_passed is True
    assert candidate.core_evidence_mode == CORE_MODE_SUPPORTED
    assert candidate.core_evidence["dimensions"]["stage"]["matched_concepts"] == [
        "completed_state",
        "woodwork",
    ]
    assert result.decision == "matched"
    assert result.tags == tags


def test_highest_final_score_wins_over_semantically_supported_group() -> None:
    supported = _candidate("specific", ["木工完工"], 0.82, 0.82)
    supported = ScoredCandidate(
        **{
            **supported.__dict__,
            "core_evidence_mode": CORE_MODE_SUPPORTED,
            "core_requirements_passed": True,
        }
    )
    fallback = _candidate("generic", ["日常", "巡查工地"], 0.96, 0.94)
    fallback = ScoredCandidate(
        **{
            **fallback.__dict__,
            "core_evidence_mode": CORE_MODE_FALLBACK,
            "core_requirements_passed": True,
            "group_prototype_count": 3,
            "group_support_count": 3,
        }
    )
    policy = _policy().model_copy(
        update={
            "similarity_group_matching_enabled": True,
            "similarity_group_fallback_auto_threshold": 0.92,
        }
    )

    result = decide_similarity(candidates=[fallback, supported], settings=policy)

    assert result.decision == "matched"
    assert result.matched_asset_id == "generic"
    assert result.tags == ["日常", "巡查工地"]


def test_generic_group_adopts_by_score_without_additional_support_threshold() -> None:
    fallback = _candidate("generic", ["日常", "巡查工地"], 0.96, 0.94)
    fallback = ScoredCandidate(
        **{
            **fallback.__dict__,
            "core_evidence_mode": CORE_MODE_FALLBACK,
            "core_requirements_passed": True,
            "feature_reliability": 0.9,
            "feature_coverage": 0.8,
            "group_prototype_count": 1,
            "group_support_count": 1,
        }
    )
    policy = _policy().model_copy(
        update={
            "similarity_group_matching_enabled": True,
            "similarity_group_fallback_auto_threshold": 0.92,
            "similarity_group_fallback_min_support": 2,
        }
    )

    result = decide_similarity(candidates=[fallback], settings=policy)

    assert result.decision == "matched"
    assert result.tags == ["日常", "巡查工地"]


def test_screenshot_regression_adopts_7566_percent_despite_close_semantic_rank() -> None:
    first = replace(
        _candidate("paint", ["日常", "油漆完工"], 0.7566063980261485, None),
        core_evidence_mode=CORE_MODE_UNSUPPORTED,
        core_requirements_passed=False,
    )
    second = replace(
        _candidate("wood", ["木工完工"], 0.7048, None),
        final_score=0.6800918544264238,
        ranking_score=0.7310918544264238,
        core_evidence_mode=CORE_MODE_SUPPORTED,
    )
    policy = _policy().model_copy(update={
        "similarity_auto_threshold": 0.70,
        "similarity_min_margin": 0.03,
        "similarity_group_unsupported_auto_threshold": 0.85,
    })
    result = decide_similarity(candidates=[second, first], settings=policy)
    assert result.decision == "matched"
    assert result.matched_asset_id == "paint"
    assert result.tags == ["日常", "油漆完工"]
    assert result.candidates[0]["ranking_score"] == 0.7566


def test_semantic_bonus_cannot_choose_a_lower_final_score() -> None:
    high = _candidate("high", ["油漆完工"], 0.75, None)
    low = replace(_candidate("low", ["木工完工"], 0.72, None), ranking_score=0.78)
    result = decide_similarity(candidates=[low, high], settings=_policy())
    assert result.matched_asset_id == "high"


def test_tied_scores_match_deterministically_regardless_of_input_order() -> None:
    first = _candidate("a", ["厨房"], 0.70, None)
    second = _candidate("b", ["客厅"], 0.70, None)
    policy = _policy().model_copy(update={"similarity_auto_threshold": 0.70})
    for candidates in ([first, second], [second, first]):
        result = decide_similarity(candidates=candidates, settings=policy)
        assert result.decision == "matched"
        assert result.matched_asset_id == "a"
        assert result.candidate_margin == 0


def test_empty_candidates_do_not_invent_tags() -> None:
    result = decide_similarity(candidates=[], settings=_policy())
    assert result.decision == "unmatched"
    assert result.tags == []


def _candidate(
    asset_id: str,
    tags: list[str],
    image_score: float,
    content_score: float | None,
    sha256: str | None = None,
    group: LibraryAssetGroup | None = None,
) -> ScoredCandidate:
    group = group or LibraryAssetGroup(
        id=f"grp_{asset_id}", tags=tags, tag_key="\x1f".join(tags), status="active"
    )
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
