from pathlib import Path

import pytest

from src.core.config import Settings
from src.services.profiles import ProfileLoader, ProfileNotFoundError


def test_project_does_not_ship_fixed_business_processing_profiles() -> None:
    loader = ProfileLoader(Settings(profiles_directory="profiles"))

    assert loader.list_filter_profiles() == []
    assert loader.list_beautify_profiles() == []


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
