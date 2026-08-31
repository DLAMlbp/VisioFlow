from __future__ import annotations

from dataclasses import dataclass

from src.services.profiles import BeautifyProfile


@dataclass(frozen=True)
class BeautifyPolicyResult:
    profile: BeautifyProfile
    corrections: tuple[str, ...]


def validate_beautify_plan(
    profile: BeautifyProfile,
    *,
    needed: bool,
    parameters: dict[str, object],
) -> BeautifyPolicyResult:
    """Turn an AI plan into a deterministic, composition-safe execution profile."""
    neutral = _neutralize(profile)
    if not needed:
        return BeautifyPolicyResult(neutral, ())

    candidate = BeautifyProfile.model_validate(
        {**neutral.model_dump(), **parameters}
    )
    if not _has_effective_change(candidate):
        raise ValueError("AI 标记需要美化，但没有返回有效的美化调整")

    updates: dict[str, object] = {}
    corrections: list[str] = []

    # The current straighten implementation rotates and crops. Dynamic plans
    # promise to preserve every pixel and therefore cannot enable it.
    if candidate.auto_straighten:
        updates["auto_straighten"] = False
        corrections.append("为保留原始构图，已关闭自动拉直")

    if candidate.brightness >= 1.08:
        minimum_recovery = min(0.35, max(0.1, (candidate.brightness - 1) * 1.25))
        if candidate.highlight_recovery < minimum_recovery:
            updates["highlight_recovery"] = round(minimum_recovery, 3)
            corrections.append("亮度提升较多，已同步加强高光保护")

    contrast_limit = 1.5
    if candidate.shadow_lift >= 0.18:
        contrast_limit = min(contrast_limit, 1.08)
        corrections.append("阴影提升较多，已限制全局对比度")
    if candidate.local_tone_strength >= 0.25:
        contrast_limit = min(contrast_limit, 1.08)
        corrections.append("局部层次较强，已限制全局对比度")
    if candidate.contrast > contrast_limit:
        updates["contrast"] = contrast_limit
    else:
        corrections = [
            reason
            for reason in corrections
            if not reason.endswith("已限制全局对比度")
        ]

    if candidate.denoise_strength >= 0.25:
        denoise_corrected = False
        if candidate.sharpness > 1.15:
            updates["sharpness"] = 1.15
            denoise_corrected = True
        if candidate.local_clarity_strength > 0.18:
            updates["local_clarity_strength"] = 0.18
            denoise_corrected = True
        if denoise_corrected:
            corrections.append("降噪较强，已限制锐化和局部清晰度")

    corrected = candidate.model_copy(update=updates)
    return BeautifyPolicyResult(corrected, tuple(corrections))


def _neutralize(profile: BeautifyProfile) -> BeautifyProfile:
    return profile.model_copy(
        update={
            "brightness": 1.0,
            "contrast": 1.0,
            "color": 1.0,
            "sharpness": 1.0,
            "auto_white_balance": False,
            "white_balance_strength": 0.0,
            "shadow_lift": 0.0,
            "highlight_recovery": 0.0,
            "denoise_strength": 0.0,
            "local_tone_strength": 0.0,
            "local_tone_clip_limit": 1.5,
            "glare_reduction_strength": 0.0,
            "local_clarity_strength": 0.0,
            "auto_straighten": False,
            "max_straighten_degrees": 3.0,
        }
    )


def _has_effective_change(profile: BeautifyProfile) -> bool:
    multipliers = (profile.brightness, profile.contrast, profile.color, profile.sharpness)
    strengths = (
        profile.shadow_lift,
        profile.highlight_recovery,
        profile.denoise_strength,
        profile.local_tone_strength,
        profile.glare_reduction_strength,
        profile.local_clarity_strength,
    )
    white_balance_enabled = (
        profile.auto_white_balance and profile.white_balance_strength >= 0.02
    )
    return (
        any(abs(value - 1.0) >= 0.02 for value in multipliers)
        or any(value >= 0.02 for value in strengths)
        or white_balance_enabled
    )
