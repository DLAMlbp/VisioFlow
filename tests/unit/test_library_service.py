from unittest.mock import AsyncMock, Mock

import pytest

from src.models.library_asset import LibraryAsset
from src.models.library_asset_group import LibraryAssetGroup
from src.schemas.library import LibraryAssetBulkDeleteRequest, LibraryAssetGroupCreate
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


async def test_bulk_delete_assets_deletes_selected_assets_and_refreshes_groups_once() -> None:
    first_group = _group("grp_first", ["客厅"])
    second_group = _group("grp_second", ["厨房"])
    assets = [
        LibraryAsset(
            id="ast_first",
            original_object_key="uploads/first.png",
            thumbnail_object_key="library-thumbnails/first.jpg",
            group_id=first_group.id,
        ),
        LibraryAsset(
            id="ast_second",
            original_object_key="uploads/second.png",
            group_id=second_group.id,
        ),
    ]
    repository = AsyncMock()
    repository.list_assets_for_deletion.return_value = assets
    storage = AsyncMock()
    storage.delete_many.return_value = set()
    prototype_publisher = Mock()

    result = await LibraryService(
        repository,
        prototype_task_publisher=prototype_publisher,
        storage_provider=storage,
    ).bulk_delete_assets(
        LibraryAssetBulkDeleteRequest(asset_ids=["ast_first", "ast_second"])
    )

    assert result.deleted_count == 2
    assert result.failed_count == 0
    storage.delete_many.assert_awaited_once_with(
        ["uploads/first.png", "library-thumbnails/first.jpg", "uploads/second.png"]
    )
    repository.delete_assets.assert_awaited_once_with(["ast_first", "ast_second"])
    repository.mark_group_prototypes_stale.assert_awaited_once_with(
        ["grp_first", "grp_second"]
    )
    assert [call.args for call in prototype_publisher.publish.call_args_list] == [
        ("grp_first",),
        ("grp_second",),
    ]


async def test_bulk_delete_assets_keeps_records_with_storage_failures() -> None:
    group = _group("grp_test", ["施工"])
    assets = [
        LibraryAsset(
            id="ast_ok",
            original_object_key="uploads/ok.png",
            group_id=group.id,
        ),
        LibraryAsset(
            id="ast_failed",
            original_object_key="uploads/failed.png",
            thumbnail_object_key="library-thumbnails/failed.jpg",
            group_id=group.id,
        ),
    ]
    repository = AsyncMock()
    repository.list_assets_for_deletion.return_value = assets
    storage = AsyncMock()
    storage.delete_many.return_value = {"uploads/failed.png"}

    result = await LibraryService(repository, storage_provider=storage).bulk_delete_assets(
        LibraryAssetBulkDeleteRequest(delete_all=True, group_id=group.id)
    )

    assert result.deleted_count == 1
    assert result.failed_asset_ids == ["ast_failed"]
    repository.get_group.assert_awaited_once_with(group.id)
    repository.delete_assets.assert_awaited_once_with(["ast_ok"])
    repository.mark_group_prototypes_stale.assert_awaited_once_with([group.id])


async def test_bulk_delete_assets_can_delete_the_entire_library() -> None:
    repository = AsyncMock()
    repository.list_assets_for_deletion.return_value = []
    storage = AsyncMock()
    storage.delete_many.return_value = set()

    result = await LibraryService(repository, storage_provider=storage).bulk_delete_assets(
        LibraryAssetBulkDeleteRequest(delete_all=True)
    )

    assert result.deleted_count == 0
    repository.get_group.assert_not_awaited()
    repository.list_assets_for_deletion.assert_awaited_once_with(
        asset_ids=None,
        group_id=None,
    )
    repository.delete_assets.assert_awaited_once_with([])


async def test_bulk_delete_assets_rejects_an_unknown_group_before_storage_deletion() -> None:
    repository = AsyncMock()
    repository.get_group.return_value = None
    storage = AsyncMock()

    with pytest.raises(LibraryNotFound, match="素材组不存在"):
        await LibraryService(repository, storage_provider=storage).bulk_delete_assets(
            LibraryAssetBulkDeleteRequest(delete_all=True, group_id="grp_missing")
        )

    repository.list_assets_for_deletion.assert_not_awaited()
    storage.delete_many.assert_not_awaited()


async def test_bulk_delete_assets_rejects_missing_selected_assets() -> None:
    repository = AsyncMock()
    repository.list_assets_for_deletion.return_value = []
    storage = AsyncMock()

    with pytest.raises(LibraryNotFound, match="部分素材不存在"):
        await LibraryService(repository, storage_provider=storage).bulk_delete_assets(
            LibraryAssetBulkDeleteRequest(asset_ids=["ast_missing"])
        )

    storage.delete_many.assert_not_awaited()
    repository.delete_assets.assert_not_awaited()


async def test_reindex_failed_assets_resets_and_publishes_every_failed_asset() -> None:
    repository = AsyncMock()
    repository.get_group.return_value = _group("grp_test", ["施工"])
    repository.reset_failed_assets.return_value = [
        ("ast_first", "grp_test"),
        ("ast_second", "grp_test"),
    ]
    task_publisher = Mock()
    prototype_publisher = Mock()

    response = await LibraryService(
        repository,
        task_publisher=task_publisher,
        prototype_task_publisher=prototype_publisher,
    ).reindex_failed_assets(group_id="grp_test")

    assert response.queued_count == 2
    repository.reset_failed_assets.assert_awaited_once_with(group_id="grp_test")
    repository.mark_group_prototypes_stale.assert_awaited_once_with(["grp_test"])
    assert [call.args for call in task_publisher.publish.call_args_list] == [
        ("ast_first",),
        ("ast_second",),
    ]
    prototype_publisher.publish.assert_called_once_with("grp_test")


async def test_reindex_failed_assets_rejects_unknown_group() -> None:
    repository = AsyncMock()
    repository.get_group.return_value = None

    with pytest.raises(LibraryNotFound, match="素材组不存在"):
        await LibraryService(repository).reindex_failed_assets(group_id="grp_missing")

    repository.reset_failed_assets.assert_not_awaited()
