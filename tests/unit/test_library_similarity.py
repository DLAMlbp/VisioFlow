from io import BytesIO

import pytest
from PIL import Image, ImageFilter

from src.models.library_asset import LibraryAsset
from src.services.images.similarity import (
    ScoredCandidate,
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
    assert prepared.height == 24
    assert prepared.content_type == "image/jpeg"
    assert prepared.normalized_bytes.startswith(b"\xff\xd8\xff")


def test_feature_similarity_compares_scene_fields_and_subjects() -> None:
    query = {
        "scene": "住宅室内",
        "space": "厨房",
        "condition": "装修前",
        "content_type": "环境展示",
        "view": "空间全景",
        "subjects": ["橱柜", "墙砖"],
    }
    candidate = {
        **query,
        "subjects": ["橱柜", "灶台"],
    }

    assert 0.8 < feature_similarity(query, candidate) < 1.0


def test_feature_similarity_normalizes_equivalent_chinese_scene_phrases() -> None:
    query = {
        "scene": "住宅室内",
        "space": "餐厅及玄关",
        "condition": "装修完成且整洁",
        "content_type": "环境展示",
        "subjects": ["餐桌", "餐椅", "定制柜", "咖啡机", "鞋柜", "入户门", "吊灯"],
        "view": "室内空间全景",
    }
    candidate = {
        "scene": "住宅室内",
        "space": "客餐厅",
        "condition": "装修完成",
        "content_type": "环境展示",
        "subjects": ["餐桌", "餐椅", "电视", "电视柜", "沙发", "茶几", "落地窗", "吊灯", "绿植"],
        "view": "空间全景",
    }

    assert feature_similarity(query, candidate) > 0.8


def test_calibrated_policy_matches_real_dining_room_sample() -> None:
    top_feature = feature_similarity(
        {
            "scene": "住宅室内",
            "space": "餐厅及玄关",
            "condition": "装修完成且整洁",
            "content_type": "环境展示",
            "subjects": ["餐桌", "餐椅", "定制柜", "咖啡机", "鞋柜", "入户门", "吊灯"],
            "view": "室内空间全景",
        },
        {
            "scene": "住宅室内",
            "space": "客餐厅",
            "condition": "装修完成",
            "content_type": "环境展示",
            "subjects": ["餐桌", "餐椅", "电视", "电视柜", "沙发", "茶几", "落地窗", "吊灯", "绿植"],
            "view": "空间全景",
        },
    )
    candidates = [
        _scored_candidate(
            "ast_dining", ["完工案例", "旧房翻新", "客餐厅"], similarity=0.7535, feature=top_feature
        ),
        _scored_candidate(
            "ast_living", ["完工案例", "精装"], similarity=0.6520, feature=0.7738
        ),
    ]

    result = decide_similarity(
        candidates=candidates,
        settings=_policy(),
    )

    assert result.decision == "matched"
    assert result.tag_path == ["完工案例", "旧房翻新", "客餐厅"]


def test_similarity_decision_matches_high_confidence_path() -> None:
    settings = _policy()
    candidates = [
        _candidate("ast_1", ["完工案例", "厨房"], 0.92),
        _candidate("ast_2", ["完工案例", "客餐厅"], 0.71),
    ]

    result = decide_similarity(candidates=candidates, settings=settings)

    assert result.decision == "matched"
    assert result.tag_path == ["完工案例", "厨房"]
    assert result.matched_asset_id == "ast_1"


def test_similarity_decision_does_not_require_candidate_margin_above_75_percent() -> None:
    settings = _policy()
    candidates = [
        _candidate("ast_1", ["完工案例", "厨房"], 0.84),
        _candidate("ast_2", ["完工案例", "客餐厅"], 0.80),
    ]

    result = decide_similarity(candidates=candidates, settings=settings)

    assert result.decision == "matched"
    assert result.tag_path == ["完工案例", "厨房"]


@pytest.mark.parametrize(
    ("similarity", "feature", "expected"),
    [
        (0.5999, 0.90, "pending_review"),
        (0.70, 0.10, "pending_review"),
        (0.60, 0.60, "matched"),
        (0.75, 0.75, "matched"),
        (0.7501, 0.7501, "matched"),
        (0.80, 0.50, "matched"),
        (0.70, 1.00, "matched"),
    ],
)
def test_similarity_decision_uses_requested_boundaries(
    similarity: float, feature: float, expected: str
) -> None:
    result = decide_similarity(
        candidates=[
            _scored_candidate(
                "ast_1", ["完工案例", "厨房"], similarity=similarity, feature=feature
            )
        ],
        settings=_policy(),
    )

    assert result.decision == expected


def test_similarity_uses_most_similar_asset_as_match_evidence() -> None:
    settings = _policy()
    path = ["完工案例", "厨房"]
    most_similar = _scored_candidate("ast_1", path, similarity=0.91, feature=0.70)
    higher_final_score = _scored_candidate("ast_2", path, similarity=0.86, feature=1.0)

    result = decide_similarity(
        candidates=[higher_final_score, most_similar], settings=settings
    )

    assert result.decision == "matched"
    assert result.matched_asset_id == "ast_1"


def test_similarity_uses_highest_image_similarity_candidate_for_both_thresholds() -> None:
    most_similar = _scored_candidate(
        "ast_similarity", ["完工案例", "厨房"], similarity=0.76, feature=0.75
    )
    higher_final_score = _scored_candidate(
        "ast_feature", ["完工案例", "客餐厅"], similarity=0.74, feature=1.0
    )

    result = decide_similarity(
        candidates=[higher_final_score, most_similar], settings=_policy()
    )

    assert result.decision == "matched"
    assert result.matched_asset_id == "ast_similarity"
    assert result.tag_path == ["完工案例", "厨房"]


def test_similarity_matches_when_image_is_71_and_candidate_score_is_76() -> None:
    result = decide_similarity(
        candidates=[
            _scored_candidate(
                "ast_balcony", ["完工案例", "阳台"], similarity=0.7154, feature=0.875
            )
        ],
        settings=_policy(),
    )

    assert result.final_score == pytest.approx(0.76328)
    assert result.decision == "matched"


def test_similarity_decision_reviews_when_either_score_is_below_60_percent() -> None:
    settings = _policy()

    result = decide_similarity(
        candidates=[_candidate("ast_1", ["完工案例", "厨房"], 0.52)],
        settings=settings,
    )

    assert result.decision == "pending_review"
    assert result.tag_path == ["完工案例", "厨房"]
    assert result.matched_asset_id == "ast_1"
    assert result.message == "无法识别，等待人工复核"


def _candidate(asset_id: str, path: list[str], score: float) -> ScoredCandidate:
    return _scored_candidate(asset_id, path, similarity=score, feature=score)


def _scored_candidate(
    asset_id: str, path: list[str], *, similarity: float, feature: float
) -> ScoredCandidate:
    asset = LibraryAsset(
        id=asset_id,
        original_object_key=f"uploads/{asset_id}.jpg",
        leaf_tag_node_id=f"node_{asset_id}",
        status="active",
    )
    return ScoredCandidate(
        asset=asset,
        tag_path=path,
        similarity_score=similarity,
        feature_score=feature,
        final_score=similarity * 0.7 + feature * 0.3,
    )


def _policy(**updates) -> SimilarityProfile:
    values = {
        "id": "test_similarity",
        "version": 1,
        "description": "test",
        "similarity_candidate_limit": 20,
        "similarity_image_weight": 0.7,
        "similarity_feature_weight": 0.3,
        "similarity_auto_threshold": 0.60,
        "similarity_review_threshold": 0.60,
        "similarity_min_margin": 0.0,
    }
    values.update(updates)
    return SimilarityProfile.model_validate(values)
