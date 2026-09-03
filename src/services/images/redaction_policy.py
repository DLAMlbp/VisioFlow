from __future__ import annotations

from dataclasses import dataclass

from src.services.images.processing_vision import RedactionAssessment
from src.services.profiles import RedactionProfile


@dataclass(frozen=True)
class GroundFilmDecision:
    rejected: bool = False
    review_required: bool = False
    reason: str = ""
    coverage_ratio: float | None = None


def evaluate_ground_film(
    profile: RedactionProfile,
    assessment: RedactionAssessment | None,
) -> GroundFilmDecision:
    policy = profile.branded_ground_film
    if not policy.enabled:
        return GroundFilmDecision(reason="品牌地膜筛选未启用")
    if assessment is None:
        return GroundFilmDecision(
            review_required=policy.uncertain_action == "manual_review",
            reason="未获得品牌地膜识别结果，需人工确认",
        )
    ground = assessment.branded_ground_film
    if not ground.detected or not ground.brand_detected:
        return GroundFilmDecision(
            reason="未发现当家APP品牌地膜",
            coverage_ratio=ground.coverage_ratio,
        )
    if ground.confidence < policy.min_confidence:
        return GroundFilmDecision(
            review_required=policy.uncertain_action == "manual_review",
            reason=f"品牌地膜识别置信度不足：{ground.reason}",
            coverage_ratio=ground.coverage_ratio,
        )
    if ground.coverage_ratio >= policy.reject_coverage_gte:
        return GroundFilmDecision(
            rejected=True,
            reason=(
                f"当家APP品牌地膜占画面{ground.coverage_ratio:.1%}，"
                f"达到不合格阈值{policy.reject_coverage_gte:.1%}"
            ),
            coverage_ratio=ground.coverage_ratio,
        )
    review_floor = max(0.0, policy.reject_coverage_gte - policy.review_margin)
    if ground.coverage_ratio >= review_floor:
        return GroundFilmDecision(
            review_required=policy.uncertain_action == "manual_review",
            reason=(
                f"当家APP品牌地膜占画面{ground.coverage_ratio:.1%}，"
                "接近不合格阈值，需人工确认"
            ),
            coverage_ratio=ground.coverage_ratio,
        )
    return GroundFilmDecision(
        reason=f"当家APP品牌地膜占画面{ground.coverage_ratio:.1%}，低于不合格阈值",
        coverage_ratio=ground.coverage_ratio,
    )
