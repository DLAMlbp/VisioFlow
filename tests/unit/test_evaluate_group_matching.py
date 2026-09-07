import io
import json

from scripts.evaluate_group_matching import evaluate, load_assets
from src.services.profiles import SimilarityProfile


def _profile() -> SimilarityProfile:
    return SimilarityProfile.model_validate(
        {
            "id": "test",
            "version": 1,
            "description": "test",
            "similarity_candidate_limit": 20,
            "similarity_image_weight": 1.0,
            "similarity_feature_weight": 0.0,
            "similarity_auto_threshold": 0.80,
            "similarity_review_threshold": 0.60,
            "similarity_min_margin": 0.05,
            "similarity_group_matching_enabled": True,
            "similarity_group_max_prototypes": 2,
            # This synthetic vector-only fixture validates evaluator arithmetic.
            # Production fallback groups intentionally require stronger evidence.
            "similarity_group_fallback_auto_enabled": True,
            "similarity_group_fallback_auto_threshold": 0.80,
            "similarity_group_fallback_min_margin": 0.05,
            "similarity_group_fallback_min_support": 1,
            "similarity_group_fallback_min_feature_score": 0.0,
            "similarity_group_fallback_min_feature_strength": 0.0,
        }
    )


def test_leave_one_out_evaluation_measures_complete_group_precision() -> None:
    rows = [
        {
            "id": "a1",
            "group_id": "ga",
            "tags": ["完整组A", "空间A"],
            "embedding": [1.0, 0.0],
        },
        {
            "id": "a2",
            "group_id": "ga",
            "tags": ["完整组A", "空间A"],
            "embedding": [0.99, 0.01],
        },
        {
            "id": "b1",
            "group_id": "gb",
            "tags": ["完整组B", "空间B"],
            "embedding": [0.0, 1.0],
        },
        {
            "id": "b2",
            "group_id": "gb",
            "tags": ["完整组B", "空间B"],
            "embedding": [0.01, 0.99],
        },
    ]
    assets = load_assets(io.StringIO("".join(json.dumps(row) + "\n" for row in rows)))

    report = evaluate(assets, profile=_profile(), target_precision=0.95)

    assert report["status"] == "passed"
    assert report["evaluated_count"] == 4
    assert report["automatic_precision"] == 1.0
    assert report["automatic_coverage"] == 1.0
