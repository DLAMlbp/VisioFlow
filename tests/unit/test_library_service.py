from unittest.mock import AsyncMock

import pytest

from src.models.library_asset import LibraryAsset
from src.models.library_asset_group import LibraryAssetGroup
from src.schemas.library import LibraryAssetGroupCreate, TagReviewDecisionRequest
from src.services.library import InvalidLibraryRequest, LibraryNotFound, LibraryService


async def test_groups_report_direct_asset_counts_without_tree_rollup() -> None:
    groups = [
        _group("grp_living", ["客厅", "现代", "完工"]),
        _group("grp_kitchen", ["厨房", "明亮"]),
    ]
    repository = AsyncMock()
    repository.list_groups.return_value = groups
    repository.group_asset_counts.return_value = {
        "grp_living": 4,
        "grp_kitchen": 2,
    }

    result = await LibraryService(repository).get_groups()

    assert result[0].tags == ["客厅", "现代", "完工"]
    assert result[0].asset_count == 4
    assert result[1].tags == ["厨房", "明亮"]
    assert result[1].asset_count == 2


async def test_create_group_rejects_an_existing_tag_combination() -> None:
    repository = AsyncMock()
    repository.find_group_by_tag_key.return_value = _group("grp_existing", ["客厅", "完工"])

    with pytest.raises(InvalidLibraryRequest, match="相同的标签组合已存在"):
        await LibraryService(repository).create_group(
            LibraryAssetGroupCreate(tags=["客厅", "完工"])
        )

    repository.create_group.assert_not_awaited()


def _group(group_id: str, tags: list[str]) -> LibraryAssetGroup:
    return LibraryAssetGroup(
        id=group_id,
        tags=tags,
        tag_key="\x1f".join(tag.casefold() for tag in tags),
        sort_order=0,
        status="active",
    )


async def test_delete_asset_removes_object_before_database_record() -> None:
    group = _group("grp_test", ["客厅", "完工"])
    asset = LibraryAsset(
        id="ast_test",
        original_object_key="uploads/2026/08/28/test.png",
        thumbnail_object_key="library-thumbnails/ast_test.jpg",
        group_id=group.id,
        group=group,
        status="active",
    )
    repository = AsyncMock()
    repository.get_asset.return_value = asset
    storage = AsyncMock()

    await LibraryService(repository, storage_provider=storage).delete_asset(asset.id)

    assert storage.delete.await_args_list == [
        ((asset.thumbnail_object_key,),),
        ((asset.original_object_key,),),
    ]
    repository.delete_asset.assert_awaited_once_with(asset)


async def test_delete_asset_rejects_unknown_asset() -> None:
    repository = AsyncMock()
    repository.get_asset.return_value = None
    storage = AsyncMock()

    with pytest.raises(LibraryNotFound, match="素材不存在"):
        await LibraryService(repository, storage_provider=storage).delete_asset("ast_missing")

    storage.delete.assert_not_awaited()
    repository.delete_asset.assert_not_awaited()


def _match(decision: str, asset_id: str | None = None):
    return type(
        "Match",
        (),
        {
            "image_id": "img_test",
            "matched_asset_id": asset_id,
            "matched_tags_snapshot": ["客厅"] if decision == "matched" else [],
            "similarity_score": 0.9,
            "feature_score": None,
            "final_score": 0.9,
            "decision": decision,
            "message": "已确认",
            "candidate_json": [],
        },
    )()


async def test_review_exact_duplicate_is_idempotent() -> None:
    repository = AsyncMock()
    repository.get_match.return_value = _match("matched", "ast_test")

    response = await LibraryService(repository).decide_review(
        "img_test",
        TagReviewDecisionRequest(decision="matched", matched_asset_id="ast_test"),
    )

    assert response.decision == "matched"
    repository.upsert_match.assert_not_awaited()


async def test_review_conflicting_final_decision_is_rejected() -> None:
    repository = AsyncMock()
    repository.get_match.return_value = _match("unmatched")

    with pytest.raises(InvalidLibraryRequest, match="已经确认"):
        await LibraryService(repository).decide_review(
            "img_test",
            TagReviewDecisionRequest(decision="matched", matched_asset_id="ast_test"),
        )

    repository.upsert_match.assert_not_awaited()
