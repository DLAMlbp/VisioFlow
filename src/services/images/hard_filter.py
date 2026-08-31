from __future__ import annotations

from dataclasses import dataclass

from src.core.config import Settings


class RejectCode:
    IMAGE_TOO_SMALL = "IMAGE_TOO_SMALL"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    EXTREME_BLUR = "EXTREME_BLUR"
    EXTREME_OVEREXPOSURE = "EXTREME_OVEREXPOSURE"
    EXTREME_UNDEREXPOSURE = "EXTREME_UNDEREXPOSURE"
    LOCAL_HEAVY_SHADOW = "LOCAL_HEAVY_SHADOW"
    QUALITY_SCORE_TOO_LOW = "QUALITY_SCORE_TOO_LOW"
    SHARPNESS_SCORE_TOO_LOW = "SHARPNESS_SCORE_TOO_LOW"
    EXPOSURE_SCORE_TOO_LOW = "EXPOSURE_SCORE_TOO_LOW"
    CONTRAST_SCORE_TOO_LOW = "CONTRAST_SCORE_TOO_LOW"
    NOISE_SCORE_TOO_LOW = "NOISE_SCORE_TOO_LOW"
    SOLID_COLOR = "SOLID_COLOR"
    DUPLICATE_IMAGE = "DUPLICATE_IMAGE"
    AI_FILTER_REJECTED = "AI_FILTER_REJECTED"
    COMPLETION_INVALID = "COMPLETION_INVALID_OR_IRRELEVANT"
    COMPLETION_INSUFFICIENT = "COMPLETION_INSUFFICIENT_EVIDENCE"
    COMPLETION_LOW_CONFIDENCE = "COMPLETION_LOW_CONFIDENCE"


@dataclass(frozen=True)
class HardFilterResult:
    reject_codes: tuple[str, ...]
    warning_codes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.reject_codes


class HardFilterService:
    """Apply only resource-safety limits before the AI business decision."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(self, thumbnail_bytes: bytes, *, width: int, height: int) -> HardFilterResult:
        del thumbnail_bytes
        if (
            width > self.settings.hard_filter_max_width
            or height > self.settings.hard_filter_max_height
        ):
            return HardFilterResult((RejectCode.IMAGE_TOO_LARGE,))
        return HardFilterResult(())
