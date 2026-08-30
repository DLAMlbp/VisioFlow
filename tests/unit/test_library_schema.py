import pytest
from pydantic import ValidationError

from src.schemas.library import LibraryAssetGroupCreate, LibraryAssetGroupUpdate


def test_group_tags_are_trimmed_and_deduplicated_without_creating_a_hierarchy() -> None:
    payload = LibraryAssetGroupCreate(tags=[" 客厅 ", "现代风格", "客厅", "完工"])

    assert payload.tags == ["客厅", "现代风格", "完工"]


def test_group_requires_at_least_one_non_empty_tag() -> None:
    with pytest.raises(ValidationError, match="标签不能为空"):
        LibraryAssetGroupCreate(tags=["  "])


def test_group_update_accepts_a_complete_replacement_tag_set() -> None:
    payload = LibraryAssetGroupUpdate(tags=["厨房", "明亮", "完工"])

    assert payload.tags == ["厨房", "明亮", "完工"]
