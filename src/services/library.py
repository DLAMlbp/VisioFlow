from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError
from src.db.session import get_db_session
from src.models.image_ai_tag import ImageAITag
from src.models.library_asset import LibraryAsset
from src.models.library_asset_group import LibraryAssetGroup
from src.repositories.library import LibraryRepository
from src.schemas.library import (
    LibraryAssetCreate,
    LibraryAssetGroupCreate,
    LibraryAssetGroupResponse,
    LibraryAssetGroupUpdate,
    LibraryAssetListResponse,
    LibraryAssetResponse,
    LibraryAssetUpdate,
    TagReviewDecisionRequest,
    TagReviewResponse,
)
from src.services.jobs.dispatch import LibraryAssetTaskPublisher
from src.services.storage.factory import get_storage_provider
from src.services.storage.interfaces import StorageProvider


class LibraryNotFound(AppError):
    code = "LIBRARY_NOT_FOUND"


class InvalidLibraryRequest(AppError):
    code = "INVALID_LIBRARY_REQUEST"


class LibraryService:
    def __init__(
        self,
        repository: LibraryRepository,
        task_publisher: LibraryAssetTaskPublisher | None = None,
        storage_provider: StorageProvider | None = None,
    ) -> None:
        self.repository = repository
        self.task_publisher = task_publisher
        self.storage_provider = storage_provider

    async def get_groups(self) -> list[LibraryAssetGroupResponse]:
        groups = await self.repository.list_groups()
        counts = await self.repository.group_asset_counts()
        return [self._group_response(group, counts.get(group.id, 0)) for group in groups]

    async def create_group(
        self, payload: LibraryAssetGroupCreate
    ) -> LibraryAssetGroupResponse:
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
        counts = await self.repository.group_asset_counts()
        return self._group_response(group, counts.get(group.id, 0))

    async def delete_group(self, group_id: str) -> None:
        group = await self.repository.get_group(group_id)
        if group is None:
            raise LibraryNotFound("素材组不存在")
        if await self.repository.has_assets(group.id):
            raise InvalidLibraryRequest("素材组仍有关联图片，不能删除")
        await self.repository.delete_group(group)

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
        if payload.group_id:
            await self._require_active_group(payload.group_id)
        if payload.status == "active" and (asset.embedding is None or asset.analysis_json is None):
            raise InvalidLibraryRequest("素材尚未完成分析，不能启用")
        await self.repository.update_asset(asset, values)
        loaded = await self.repository.get_asset(asset.id)
        return self._asset_response(loaded or asset)

    async def delete_asset(self, asset_id: str) -> None:
        asset = await self.repository.get_asset(asset_id)
        if asset is None:
            raise LibraryNotFound("素材不存在")
        if self.storage_provider is None:
            raise RuntimeError("素材存储服务未配置")
        if asset.thumbnail_object_key:
            await self.storage_provider.delete(asset.thumbnail_object_key)
        await self.storage_provider.delete(asset.original_object_key)
        await self.repository.delete_asset(asset)

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
        if self.task_publisher:
            self.task_publisher.publish(asset.id)
        loaded = await self.repository.get_asset(asset.id)
        return self._asset_response(loaded or asset)

    async def list_reviews(self, limit: int, offset: int) -> list[TagReviewResponse]:
        matches = await self.repository.list_pending_reviews(limit, offset)
        return [self._review_response(match) for match in matches]

    async def decide_review(
        self, image_id: str, payload: TagReviewDecisionRequest
    ) -> TagReviewResponse:
        match = await self.repository.get_match(image_id)
        if match is None:
            raise LibraryNotFound("匹配记录不存在")
        if payload.decision == "unmatched":
            values = {
                "matched_asset_id": None,
                "matched_tags_snapshot": [],
                "decision": "unmatched",
                "message": "未识别到相似的图片素材",
            }
        else:
            asset_id = payload.matched_asset_id or match.matched_asset_id
            if not asset_id:
                raise InvalidLibraryRequest("请选择要确认的素材")
            asset = await self.repository.get_asset(asset_id)
            if asset is None or asset.status != "active" or asset.group.status != "active":
                raise InvalidLibraryRequest("所选素材或素材组不存在、未完成分析或已停用")
            values = {
                "matched_asset_id": asset.id,
                "matched_tags_snapshot": list(asset.group.tags),
                "decision": "matched",
                "message": "已匹配到相似图片素材",
            }
        updated = await self.repository.upsert_match(image_id, values)
        await self._sync_ai_tag(image_id, updated.decision, updated.matched_tags_snapshot)
        return self._review_response(updated)

    async def _require_active_group(self, group_id: str) -> LibraryAssetGroup:
        group = await self.repository.get_group(group_id)
        if group is None:
            raise LibraryNotFound("素材组不存在")
        if group.status != "active":
            raise InvalidLibraryRequest("不能向已停用的素材组上传图片")
        return group

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

    async def _sync_ai_tag(self, image_id: str, decision: str, tags: list[str]) -> None:
        tag = await self.repository.session.scalar(
            select(ImageAITag).where(ImageAITag.image_id == image_id)
        )
        if tag is None:
            return
        current = dict(tag.tag_json or {})
        current.update(
            {
                "tags": tags if decision == "matched" else [],
                "categories": {"素材库标签": tags} if decision == "matched" else {},
                "candidate_tags": [],
            }
        )
        tag.tag_json = current
        await self.repository.session.commit()

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

    @staticmethod
    def _review_response(match) -> TagReviewResponse:
        return TagReviewResponse(
            image_id=match.image_id,
            matched_asset_id=match.matched_asset_id,
            tags=match.matched_tags_snapshot or [],
            similarity_score=match.similarity_score,
            feature_score=match.feature_score,
            final_score=match.final_score,
            decision=match.decision,
            message=match.message,
            candidates=match.candidate_json or [],
        )


def get_library_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LibraryService:
    return LibraryService(
        LibraryRepository(session),
        LibraryAssetTaskPublisher(),
        get_storage_provider(),
    )
