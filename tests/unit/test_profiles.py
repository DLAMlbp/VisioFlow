from pathlib import Path

import pytest

from src.core.config import Settings
from src.services.profiles import ProfileLoader, ProfileNotFoundError


def test_loads_default_renovation_profiles() -> None:
    loader = ProfileLoader(Settings(profiles_directory="profiles"))

    filter_profile = loader.get_filter_profile("renovation_submission_v1")
    beautify_profile = loader.get_beautify_profile("renovation_natural_v1")

    assert filter_profile.hard_rules.min_width == 1280
    assert beautify_profile.min_output_long_side == 2048
    assert beautify_profile.jpeg_quality == 95


def test_rejects_unknown_profile(tmp_path: Path) -> None:
    loader = ProfileLoader(Settings(profiles_directory=str(tmp_path)))

    with pytest.raises(ProfileNotFoundError, match="Profile 不存在"):
        loader.get_filter_profile("missing")


def test_similarity_profiles_exclude_generic_tagging_profile() -> None:
    loader = ProfileLoader(Settings(profiles_directory="profiles"))

    profiles = loader.list_similarity_profiles()

    assert [profile.id for profile in profiles] == [
        "library_similarity_v1",
        "library_similarity_v2",
    ]
    with pytest.raises(ProfileNotFoundError, match="不是有效的相似匹配配置"):
        loader.get_similarity_profile("auto_generic_v1")
