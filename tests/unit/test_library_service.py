from unittest.mock import AsyncMock

import pytest

from src.models.library_asset import LibraryAsset
from src.models.library_tag_node import LibraryTagNode
from src.services.library import LibraryNotFound, LibraryService


async def test_tag_tree_rolls_asset_counts_up_through_all_ancestors() -> None:
    nodes = [
        _node("root", None, "根标签", 0),
        _node("branch", "root", "二级标签", 1),
        _node("leaf_a", "branch", "末级 A", 2),
        _node("leaf_b", "branch", "末级 B", 2),
    ]
    repository = AsyncMock()
    repository.list_tag_nodes.return_value = nodes
    repository.asset_counts.return_value = {
        "root": 1,
        "leaf_a": 2,
        "leaf_b": 3,
    }

    tree = await LibraryService(repository).get_tag_tree()

    root = tree[0]
    branch = root.children[0]
    assert root.asset_count == 6
    assert branch.asset_count == 5
    assert branch.children[0].asset_count == 2
    assert branch.children[1].asset_count == 3


def _node(node_id: str, parent_id: str | None, name: str, depth: int) -> LibraryTagNode:
    return LibraryTagNode(
        id=node_id,
        parent_id=parent_id,
        name=name,
        depth=depth,
        sort_order=0,
        status="active",
    )


async def test_delete_asset_removes_object_before_database_record() -> None:
    asset = LibraryAsset(
        id="ast_test",
        original_object_key="uploads/2026/08/28/test.png",
        thumbnail_object_key="library-thumbnails/ast_test.jpg",
        leaf_tag_node_id="leaf",
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
