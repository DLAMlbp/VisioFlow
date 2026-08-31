import pytest

from src.services.images.beautify_policy import validate_beautify_plan
from src.services.profiles import BeautifyProfile


def _profile() -> BeautifyProfile:
    return BeautifyProfile(
        id="test",
        version=1,
        description="test",
        brightness=1,
        contrast=1,
        color=1,
        sharpness=1,
        jpeg_quality=90,
    )


def test_needed_plan_must_contain_an_effective_change() -> None:
    with pytest.raises(ValueError, match="没有返回有效"):
        validate_beautify_plan(_profile(), needed=True, parameters={})


def test_policy_disables_crop_based_straightening_and_protects_highlights() -> None:
    result = validate_beautify_plan(
        _profile(),
        needed=True,
        parameters={"brightness": 1.12, "auto_straighten": True},
    )

    assert result.profile.auto_straighten is False
    assert result.profile.highlight_recovery >= 0.15
    assert len(result.corrections) == 2


def test_policy_limits_conflicting_denoise_sharpness_and_clarity() -> None:
    result = validate_beautify_plan(
        _profile(),
        needed=True,
        parameters={
            "denoise_strength": 0.35,
            "sharpness": 1.7,
            "local_clarity_strength": 0.4,
        },
    )

    assert result.profile.sharpness == 1.15
    assert result.profile.local_clarity_strength == 0.18
    assert any("降噪较强" in reason for reason in result.corrections)


def test_policy_limits_global_contrast_when_local_tone_or_shadows_are_strong() -> None:
    result = validate_beautify_plan(
        _profile(),
        needed=True,
        parameters={"contrast": 1.3, "shadow_lift": 0.2, "local_tone_strength": 0.3},
    )

    assert result.profile.contrast == 1.08
    assert len([reason for reason in result.corrections if "全局对比度" in reason]) == 2
