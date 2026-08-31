from io import BytesIO

from PIL import Image, ImageFilter

from src.models.library_asset import LibraryAsset
from src.models.library_asset_group import LibraryAssetGroup
from src.services.images.similarity import (
    ScoredCandidate,
    apply_shadow_mode,
    combined_similarity_score,
    decide_similarity,
    feature_similarity,
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


def test_close_candidates_require_review_without_final_tags() -> None:
    result = decide_similarity(candidates=[
        _candidate("ast_1", ["完工", "厨房"], 0.90, 0.90),
        _candidate("ast_2", ["完工", "客厅"], 0.88, 0.88)],
        settings=_policy().model_copy(update={"similarity_min_margin": 0.05}))
    assert result.decision == "pending_review"
    assert result.tags == []
    assert result.candidates[0]["tags"] == ["完工", "厨房"]


def test_missing_content_features_require_review_without_final_tags() -> None:
    result = decide_similarity(
        candidates=[_candidate("ast_1", ["施工", "水电"], 0.78, None)],
        settings=_policy(),
    )
    assert result.decision == "pending_review"
    assert result.tags == []
    assert result.feature_score is None


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
    review = decide_similarity(
        candidates=[_candidate("ast_1", ["施工", "水电"], 0.78, None)],
        settings=_policy(),
    )

    assert apply_shadow_mode(review, enabled=True) is review
    assert apply_shadow_mode(review, enabled=False) is review


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


def _candidate(
    asset_id: str,
    tags: list[str],
    image_score: float,
    content_score: float | None,
) -> ScoredCandidate:
    group = LibraryAssetGroup(id=f"grp_{asset_id}", tags=tags, tag_key="\x1f".join(tags), status="active")
    asset = LibraryAsset(id=asset_id, original_object_key=f"uploads/{asset_id}.jpg",
        group_id=group.id, group=group, status="active")
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
        "id": "library_similarity_v2", "version": 7, "description": "hybrid",
        "similarity_candidate_limit": 20, "similarity_image_weight": 0.70,
        "similarity_feature_weight": 0.30, "similarity_auto_threshold": 0.60,
        "similarity_review_threshold": 0.60, "similarity_min_margin": 0.00})
