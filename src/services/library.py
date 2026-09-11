from __future__ import annotations

import logging
from typing import Annotated
from uuid import uuid4

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError
from src.core.metrics import emit_metric
from src.db.session import get_db_session
from src.models.library_asset import LibraryAsset
from src.models.library_asset_group import LibraryAssetGroup
from src.repositories.library import LibraryRepository
from src.schemas.library import (
    LibraryAssetBulkDeleteRequest,
    LibraryAssetBulkDeleteResponse,
    LibraryAssetCreate,
    LibraryAssetGroupBulkDeleteResponse,
    LibraryAssetGroupCreate,
    LibraryAssetGroupResponse,
    LibraryAssetGroupUpdate,
    LibraryAssetListResponse,
    LibraryAssetResponse,
    LibraryAssetUpdate,
    LibraryFailedAssetReindexResponse,
)
from src.services.jobs.dispatch import (
    LibraryAssetTaskPublisher,
    LibraryGroupPrototypeTaskPublisher,
)
from src.services.storage.factory import get_storage_provider
from src.services.storage.interfaces import StorageProvider

logger = logging.getLogger(__name__)


class LibraryNotFound(AppError):
    code = "LIBRARY_NOT_FOUND"


class InvalidLibraryRequest(AppError):
    code = "INVALID_LIBRARY_REQUEST"


class LibraryService:
    def __init__(
        self,
        repository: LibraryRepository,
        task_publisher: LibraryAssetTaskPublisher | None = None,
        prototype_task_publisher: LibraryGroupPrototypeTaskPublisher | None = None,
        storage_provider: StorageProvider | None = None,
    ) -> None:
        self.repository = repository
        self.task_publisher = task_publisher
        self.prototype_task_publisher = prototype_task_publisher
        self.storage_provider = storage_provider

    async def get_groups(self) -> list[LibraryAssetGroupResponse]:
        groups = await self.repository.list_groups()
        counts = await self.repository.group_asset_counts()
        return [self._group_response(group, counts.get(group.id, 0)) for group in groups]

    async def create_group(self, payload: LibraryAssetGroupCreate) -> LibraryAssetGroupResponse:
        tag_key = self._tag_key(payload.tags)
        if await self.repository.find_group_by_tag_key(tag_key):
            raise InvalidLibraryRequest("相同的标签组合已存在")
        group = await self.repository.create_group(
            LibraryAssetGroup(
                id=f"grp_{uuid4().hex}",
                tags=payload.tags,
                tag_key=tag_key,
                sort_order=payload.sort_order,
                status="active",
            )
        )
        return self._group_response(group)

    async def update_group(
        self, group_id: str, payload: LibraryAssetGroupUpdate
    ) -> LibraryAssetGroupResponse:
        group = await self.repository.get_group(group_id)
        if group is None:
            raise LibraryNotFound("素材组不存在")
        values = payload.model_dump(exclude_none=True)
        if payload.tags is not None:
            tag_key = self._tag_key(payload.tags)
            duplicate = await self.repository.find_group_by_tag_key(tag_key)
            if duplicate is not None and duplicate.id != group.id:
                raise InvalidLibraryRequest("相同的标签组合已存在")
            values["tag_key"] = tag_key
        group = await self.repository.update_group(group, values)
        await self._mark_prototypes_stale([group.id])
        self._publish_prototype_rebuild(group.id)
        counts = await self.repository.group_asset_counts()
        return self._group_response(group, counts.get(group.id, 0))

    async def delete_group(self, group_id: str) -> None:
        group = await self.repository.get_group(group_id)
        if group is None:
            raise LibraryNotFound("素材组不存在")
        if await self.repository.has_assets(group.id):
            raise InvalidLibraryRequest("素材组仍有关联图片，不能删除")
        await self.repository.delete_group(group)

    async def delete_all_groups(self) -> LibraryAssetGroupBulkDeleteResponse:
        counts = await self.repository.group_asset_counts()
        asset_count = sum(counts.values())
        if asset_count:
            raise InvalidLibraryRequest(f"仍有 {asset_count} 张关联图片，请先删除图片")
        groups = await self.repository.list_groups()
        group_ids = [group.id for group in groups]
        await self.repository.delete_groups(group_ids)
        return LibraryAssetGroupBulkDeleteResponse(deleted_count=len(group_ids))

    async def create_asset(self, payload: LibraryAssetCreate) -> LibraryAssetResponse:
        await self._require_active_group(payload.group_id)
        if await self.repository.get_asset_by_object_key(payload.object_key):
            raise InvalidLibraryRequest("该图片已经登记到素材库")
        asset = await self.repository.create_asset(
            LibraryAsset(
                id=f"ast_{uuid4().hex}",
                original_object_key=payload.object_key,
                original_filename=payload.original_filename,
                group_id=payload.group_id,
                status="pending",
            )
        )
        if self.task_publisher:
            self.task_publisher.publish(asset.id)
        loaded = await self.repository.get_asset(asset.id)
        return self._asset_response(loaded or asset)

    async def list_assets(
        self,
        *,
        group_id: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> LibraryAssetListResponse:
        total, assets = await self.repository.list_assets(
            group_id=group_id, status=status, limit=limit, offset=offset
        )
        return LibraryAssetListResponse(
            total=total,
            items=[self._asset_response(asset) for asset in assets],
        )

    async def update_asset(
        self, asset_id: str, payload: LibraryAssetUpdate
    ) -> LibraryAssetResponse:
        asset = await self.repository.get_asset(asset_id)
        if asset is None:
            raise LibraryNotFound("素材不存在")
        values = payload.model_dump(exclude_none=True)
        previous_group_id = asset.group_id
        if payload.group_id:
            await self._require_active_group(payload.group_id)
        if payload.status == "active" and (
            asset.embedding is None or asset.analysis_json is None
        ):
            raise InvalidLibraryRequest("素材尚未完成图片向量和内容特征分析，不能启用")
        await self.repository.update_asset(asset, values)
        await self._mark_prototypes_stale([previous_group_id, asset.group_id])
        self._publish_prototype_rebuild(previous_group_id)
        if asset.group_id != previous_group_id:
            self._publish_prototype_rebuild(asset.group_id)
        loaded = await self.repository.get_asset(asset.id)
        return self._asset_response(loaded or asset)

    async def delete_asset(self, asset_id: str) -> None:
        asset = await self.repository.get_asset(asset_id)
        if asset is None:
            raise LibraryNotFound("素材不存在")
        if self.storage_provider is None:
            raise RuntimeError("素材存储服务未配置")
        group_id = asset.group_id
        if asset.thumbnail_object_key:
            await self.storage_provider.delete(asset.thumbnail_object_key)
        await self.storage_provider.delete(asset.original_object_key)
        await self.repository.delete_asset(asset)
        await self._mark_prototypes_stale([group_id])
        self._publish_prototype_rebuild(group_id)

    async def bulk_delete_assets(
        self, payload: LibraryAssetBulkDeleteRequest
    ) -> LibraryAssetBulkDeleteResponse:
        if self.storage_provider is None:
            raise RuntimeError("素材存储服务未配置")
        if (
            payload.delete_all
            and payload.group_id is not None
            and await self.repository.get_group(payload.group_id) is None
        ):
            raise LibraryNotFound("素材组不存在")

        requested_ids = None if payload.delete_all else payload.asset_ids
        assets = await self.repository.list_assets_for_deletion(
            asset_ids=requested_ids,
            group_id=payload.group_id if payload.delete_all else None,
        )
        if requested_ids is not None:
            found_ids = {asset.id for asset in assets}
            missing_ids = [asset_id for asset_id in requested_ids if asset_id not in found_ids]
            if missing_ids:
                raise LibraryNotFound("部分素材不存在，请刷新后重试")

        object_keys = [
            object_key
            for asset in assets
            for object_key in (asset.original_object_key, asset.thumbnail_object_key)
            if object_key
        ]
        try:
            failed_keys = await self.storage_provider.delete_many(object_keys)
        except Exception:
            logger.warning("Unable to batch-delete library asset objects", exc_info=True)
            failed_keys = set(object_keys)

        failed_asset_ids = [
            asset.id
            for asset in assets
            if {asset.original_object_key, asset.thumbnail_object_key} & failed_keys
        ]
        failed_asset_id_set = set(failed_asset_ids)
        deleted_assets = [asset for asset in assets if asset.id not in failed_asset_id_set]
        deleted_ids = [asset.id for asset in deleted_assets]
        affected_group_ids = sorted({asset.group_id for asset in deleted_assets})
        await self.repository.delete_assets(deleted_ids)
        await self._mark_prototypes_stale(affected_group_ids)
        for group_id in affected_group_ids:
            self._publish_prototype_rebuild(group_id)

        emit_metric(
            logger,
            "library_assets_bulk_deleted_total",
            value=len(deleted_ids),
            labels={
                "scope": (
                    "group" if payload.group_id else "all" if payload.delete_all else "selected"
                )
            },
        )
        return LibraryAssetBulkDeleteResponse(
            deleted_count=len(deleted_ids),
            failed_count=len(failed_asset_ids),
            failed_asset_ids=failed_asset_ids,
        )

    async def reindex_asset(self, asset_id: str) -> LibraryAssetResponse:
        asset = await self.repository.get_asset(asset_id)
        if asset is None:
            raise LibraryNotFound("素材不存在")
        await self.repository.update_asset(
            asset,
            {
                "status": "pending",
                "error_message": None,
                "analysis_json": None,
                "embedding": None,
                "embedding_version": None,
            },
        )
        await self._mark_prototypes_stale([asset.group_id])
        self._publish_prototype_rebuild(asset.group_id)
        if self.task_publisher:
            self.task_publisher.publish(asset.id)
        loaded = await self.repository.get_asset(asset.id)
        return self._asset_response(loaded or asset)

    async def reindex_failed_assets(
        self, *, group_id: str | None
    ) -> LibraryFailedAssetReindexResponse:
        if group_id is not None and await self.repository.get_group(group_id) is None:
            raise LibraryNotFound("素材组不存在")
        reset_assets = await self.repository.reset_failed_assets(group_id=group_id)
        group_ids = sorted({asset_group_id for _, asset_group_id in reset_assets})
        await self._mark_prototypes_stale(group_ids)
        for asset_id, _ in reset_assets:
            if self.task_publisher:
                self.task_publisher.publish(asset_id)
        for asset_group_id in group_ids:
            self._publish_prototype_rebuild(asset_group_id)
        emit_metric(
            logger,
            "library_failed_assets_reindexed_total",
            value=len(reset_assets),
            labels={"scope": "group" if group_id else "all"},
        )
        return LibraryFailedAssetReindexResponse(queued_count=len(reset_assets))

    async def _require_active_group(self, group_id: str) -> LibraryAssetGroup:
        group = await self.repository.get_group(group_id)
        if group is None:
            raise LibraryNotFound("素材组不存在")
        if group.status != "active":
            raise InvalidLibraryRequest("不能向已停用的素材组上传图片")
        return group

    def _publish_prototype_rebuild(self, group_id: str) -> None:
        if self.prototype_task_publisher:
            self.prototype_task_publisher.publish(group_id)

    async def _mark_prototypes_stale(self, group_ids: list[str]) -> None:
        marker = getattr(self.repository, "mark_group_prototypes_stale", None)
        if marker is not None:
            await marker(group_ids)

    @staticmethod
    def _asset_response(asset: LibraryAsset) -> LibraryAssetResponse:
        return LibraryAssetResponse(
            id=asset.id,
            original_object_key=asset.original_object_key,
            thumbnail_object_key=asset.thumbnail_object_key,
            original_filename=asset.original_filename,
            group_id=asset.group_id,
            tags=list(asset.group.tags),
            content_type=asset.content_type,
            width=asset.width,
            height=asset.height,
            status=asset.status,
            error_message=asset.error_message,
            analysis=asset.analysis_json,
            created_at=asset.created_at,
        )

    @staticmethod
    def _tag_key(tags: list[str]) -> str:
        return "\x1f".join(tag.casefold() for tag in tags)

    @staticmethod
    def _group_response(
        group: LibraryAssetGroup, asset_count: int = 0
    ) -> LibraryAssetGroupResponse:
        return LibraryAssetGroupResponse(
            id=group.id,
            tags=list(group.tags),
            sort_order=group.sort_order,
            status=group.status,
            asset_count=asset_count,
        )

def get_library_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LibraryService:
    return LibraryService(
        LibraryRepository(session),
        LibraryAssetTaskPublisher(),
        LibraryGroupPrototypeTaskPublisher(),
        get_storage_provider(),
    )
