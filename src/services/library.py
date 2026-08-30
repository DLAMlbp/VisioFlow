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
from src.models.library_tag_node import LibraryTagNode
from src.repositories.library import LibraryRepository
from src.schemas.library import (
    LibraryAssetCreate,
    LibraryAssetListResponse,
    LibraryAssetResponse,
    LibraryAssetUpdate,
    LibraryTagNodeCreate,
    LibraryTagNodeResponse,
    LibraryTagNodeUpdate,
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

    async def get_tag_tree(self) -> list[LibraryTagNodeResponse]:
        nodes = await self.repository.list_tag_nodes()
        counts = await self.repository.asset_counts()
        responses = {
            node.id: LibraryTagNodeResponse(
                id=node.id,
                parent_id=node.parent_id,
                name=node.name,
                depth=node.depth,
                sort_order=node.sort_order,
                status=node.status,
                asset_count=counts.get(node.id, 0),
            )
            for node in nodes
        }
        for node in reversed(nodes):
            if node.parent_id and node.parent_id in responses:
                responses[node.parent_id].asset_count += responses[node.id].asset_count

        roots: list[LibraryTagNodeResponse] = []
        for node in nodes:
            response = responses[node.id]
            if node.parent_id and node.parent_id in responses:
                responses[node.parent_id].children.append(response)
            else:
                roots.append(response)
        return roots

    async def create_tag_node(self, payload: LibraryTagNodeCreate) -> LibraryTagNodeResponse:
        parent = None
        if payload.parent_id:
            parent = await self.repository.get_tag_node(payload.parent_id)
            if parent is None:
                raise LibraryNotFound("父级标签不存在")
            if parent.status != "active":
                raise InvalidLibraryRequest("不能在已停用标签下新增子标签")
        if await self.repository.find_sibling(payload.parent_id, payload.name):
            raise InvalidLibraryRequest("同级标签名称已存在")
        node = await self.repository.create_tag_node(
            LibraryTagNode(
                id=f"ltn_{uuid4().hex}",
                parent_id=payload.parent_id,
                name=payload.name,
                depth=(parent.depth + 1) if parent else 0,
                sort_order=payload.sort_order,
                status="active",
            )
        )
        return self._tag_response(node)

    async def update_tag_node(
        self, node_id: str, payload: LibraryTagNodeUpdate
    ) -> LibraryTagNodeResponse:
        node = await self.repository.get_tag_node(node_id)
        if node is None:
            raise LibraryNotFound("标签不存在")
        values = payload.model_dump(exclude_none=True)
        if "name" in values:
            values["name"] = str(values["name"]).strip()
            sibling = await self.repository.find_sibling(node.parent_id, str(values["name"]))
            if sibling is not None and sibling.id != node.id:
                raise InvalidLibraryRequest("同级标签名称已存在")
        node = await self.repository.update_tag_node(node, values)
        return self._tag_response(node)

    async def delete_tag_node(self, node_id: str) -> None:
        node = await self.repository.get_tag_node(node_id)
        if node is None:
            raise LibraryNotFound("标签不存在")
        if await self.repository.has_children(node.id):
            raise InvalidLibraryRequest("存在下级标签，不能删除")
        if await self.repository.has_assets(node.id):
            raise InvalidLibraryRequest("标签仍关联素材，不能删除")
        await self.repository.delete_tag_node(node)

    async def create_asset(self, payload: LibraryAssetCreate) -> LibraryAssetResponse:
        node = await self._require_uploadable_node(payload.leaf_tag_node_id)
        if await self.repository.get_asset_by_object_key(payload.object_key):
            raise InvalidLibraryRequest("该图片已经登记到素材库")
        asset = await self.repository.create_asset(
            LibraryAsset(
                id=f"ast_{uuid4().hex}",
                original_object_key=payload.object_key,
                original_filename=payload.original_filename,
                leaf_tag_node_id=node.id,
                status="pending",
            )
        )
        if self.task_publisher:
            self.task_publisher.publish(asset.id)
        return await self._asset_response(asset)

    async def list_assets(
        self,
        *,
        leaf_tag_node_id: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> LibraryAssetListResponse:
        total, assets = await self.repository.list_assets(
            leaf_tag_node_id=leaf_tag_node_id, status=status, limit=limit, offset=offset
        )
        return LibraryAssetListResponse(
            total=total,
            items=[await self._asset_response(asset) for asset in assets],
        )

    async def update_asset(
        self, asset_id: str, payload: LibraryAssetUpdate
    ) -> LibraryAssetResponse:
        asset = await self.repository.get_asset(asset_id)
        if asset is None:
            raise LibraryNotFound("素材不存在")
        values = payload.model_dump(exclude_none=True)
        if payload.leaf_tag_node_id:
            await self._require_uploadable_node(payload.leaf_tag_node_id)
        if payload.status == "active" and (asset.embedding is None or asset.analysis_json is None):
            raise InvalidLibraryRequest("素材尚未完成分析，不能启用")
        asset = await self.repository.update_asset(asset, values)
        return await self._asset_response(asset)

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
        asset = await self.repository.update_asset(
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
        return await self._asset_response(asset)

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
                "matched_tag_path_snapshot": [],
                "decision": "unmatched",
                "message": "未识别到相似的图片素材",
            }
        else:
            asset_id = payload.matched_asset_id or match.matched_asset_id
            if not asset_id:
                raise InvalidLibraryRequest("请选择要确认的素材")
            asset = await self.repository.get_asset(asset_id)
            if asset is None or asset.status != "active":
                raise InvalidLibraryRequest("所选素材不存在或已停用")
            values = {
                "matched_asset_id": asset.id,
                "matched_tag_path_snapshot": await self._tag_path(asset.leaf_tag_node_id),
                "decision": "matched",
                "message": "已匹配到相似图片素材",
            }
        updated = await self.repository.upsert_match(image_id, values)
        await self._sync_ai_tag(image_id, updated.decision, updated.matched_tag_path_snapshot)
        return self._review_response(updated)

    async def _require_uploadable_node(self, node_id: str) -> LibraryTagNode:
        node = await self.repository.get_tag_node(node_id)
        if node is None:
            raise LibraryNotFound("标签不存在")
        if node.status != "active":
            raise InvalidLibraryRequest("不能向已停用标签上传素材")
        if await self.repository.has_active_children(node.id):
            raise InvalidLibraryRequest("请选择标签树最末一级后上传素材")
        return node

    async def _asset_response(self, asset: LibraryAsset) -> LibraryAssetResponse:
        return LibraryAssetResponse(
            id=asset.id,
            original_object_key=asset.original_object_key,
            thumbnail_object_key=asset.thumbnail_object_key,
            original_filename=asset.original_filename,
            leaf_tag_node_id=asset.leaf_tag_node_id,
            tag_path=await self._tag_path(asset.leaf_tag_node_id),
            content_type=asset.content_type,
            width=asset.width,
            height=asset.height,
            status=asset.status,
            error_message=asset.error_message,
            analysis=asset.analysis_json,
            created_at=asset.created_at,
        )

    async def _tag_path(self, leaf_node_id: str) -> list[str]:
        nodes = {node.id: node for node in await self.repository.list_tag_nodes()}
        path: list[str] = []
        seen: set[str] = set()
        current = nodes.get(leaf_node_id)
        while current is not None and current.id not in seen:
            seen.add(current.id)
            path.append(current.name)
            current = nodes.get(current.parent_id) if current.parent_id else None
        return list(reversed(path))

    async def _sync_ai_tag(self, image_id: str, decision: str, path: list[str]) -> None:
        tag = await self.repository.session.scalar(
            select(ImageAITag).where(ImageAITag.image_id == image_id)
        )
        if tag is None:
            return
        current = dict(tag.tag_json or {})
        current.update(
            {
                "tags": path if decision == "matched" else [],
                "categories": {"path": path} if decision == "matched" else {},
                "candidate_tags": [],
            }
        )
        tag.tag_json = current
        await self.repository.session.commit()

    @staticmethod
    def _tag_response(node: LibraryTagNode) -> LibraryTagNodeResponse:
        return LibraryTagNodeResponse(
            id=node.id,
            parent_id=node.parent_id,
            name=node.name,
            depth=node.depth,
            sort_order=node.sort_order,
            status=node.status,
        )

    @staticmethod
    def _review_response(match) -> TagReviewResponse:
        return TagReviewResponse(
            image_id=match.image_id,
            matched_asset_id=match.matched_asset_id,
            tag_path=match.matched_tag_path_snapshot or [],
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
