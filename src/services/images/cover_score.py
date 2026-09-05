from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


COVER_SCORE_VERSION = "cover_score_v1"

CoverRiskCode = Literal[
    "unrated",
    "incomplete_scene",
    "weak_composition",
    "clutter_or_obstruction",
    "poor_perspective",
    "unrepresentative_view",
    "presentation_artifact",
]


class CoverAssessment(BaseModel):
    """Compact semantic rubric returned inside the existing vision request."""

    model_config = ConfigDict(extra="forbid")

    scene_completeness: int = Field(ge=0, le=5, strict=True)
    composition: int = Field(ge=0, le=5, strict=True)
    visual_appeal: int = Field(ge=0, le=5, strict=True)
    representativeness: int = Field(ge=0, le=5, strict=True)
    hard_fail: bool
    risk_codes: list[CoverRiskCode] = Field(max_length=4)

    @classmethod
    def unrated(cls) -> CoverAssessment:
        return cls(
            scene_completeness=0,
            composition=0,
            visual_appeal=0,
            representativeness=0,
            hard_fail=True,
            risk_codes=["unrated"],
        )


def calculate_cover_score(
    assessment: CoverAssessment,
    *,
    technical_score: float,
) -> float:
    """Return a conservative 0-100 cover score with an auditable 95+ gate."""

    technical = _clamp(technical_score)
    semantic = 20 * (
        assessment.scene_completeness * 0.30
        + assessment.composition * 0.30
        + assessment.visual_appeal * 0.25
        + assessment.representativeness * 0.15
    )
    score = semantic * 0.75 + technical * 0.25

    ratings = (
        assessment.scene_completeness,
        assessment.composition,
        assessment.visual_appeal,
        assessment.representativeness,
    )
    if assessment.hard_fail or min(ratings) <= 2:
        score = min(score, 79.99)
    elif min(ratings) == 3:
        score = min(score, 89.99)
    elif min(ratings) == 4 or technical < 80:
        score = min(score, 94.99)

    return round(_clamp(score), 2)


def cover_score_from_processing(
    processing_payload: object,
    *,
    technical_score: float,
    legacy_fallback_score: float | None = None,
) -> float:
    """Calculate a score while preserving jobs started before cover scoring existed."""

    if not isinstance(processing_payload, Mapping):
        return round(_clamp(legacy_fallback_score or 0), 2)
    raw_assessment = processing_payload.get("cover_assessment")
    if not isinstance(raw_assessment, Mapping):
        return round(_clamp(legacy_fallback_score or 0), 2)
    try:
        assessment = CoverAssessment.model_validate(raw_assessment)
    except (TypeError, ValueError, ValidationError):
        return round(_clamp(legacy_fallback_score or 0), 2)
    return calculate_cover_score(assessment, technical_score=technical_score)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))
