from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.image_similarity_match import ImageSimilarityMatch
from src.models.library_asset import LibraryAsset
from src.models.library_tag_node import LibraryTagNode


class LibraryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_tag_nodes(self) -> list[LibraryTagNode]:
        result = await self.session.execute(
            select(LibraryTagNode).order_by(
                LibraryTagNode.depth, LibraryTagNode.sort_order, LibraryTagNode.name
            )
        )
        return list(result.scalars())

    async def get_tag_node(self, node_id: str) -> LibraryTagNode | None:
        return await self.session.get(LibraryTagNode, node_id)

    async def find_sibling(self, parent_id: str | None, name: str) -> LibraryTagNode | None:
        statement = select(LibraryTagNode).where(LibraryTagNode.name == name)
        statement = statement.where(
            LibraryTagNode.parent_id == parent_id
            if parent_id is not None
            else LibraryTagNode.parent_id.is_(None)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def create_tag_node(self, node: LibraryTagNode) -> LibraryTagNode:
        self.session.add(node)
        await self.session.commit()
        await self.session.refresh(node)
        return node

    async def update_tag_node(
        self, node: LibraryTagNode, values: Mapping[str, object]
    ) -> LibraryTagNode:
        for name, value in values.items():
            setattr(node, name, value)
        await self.session.commit()
        await self.session.refresh(node)
        return node

    async def has_active_children(self, node_id: str) -> bool:
        count = await self.session.scalar(
            select(func.count()).select_from(LibraryTagNode).where(
                LibraryTagNode.parent_id == node_id,
                LibraryTagNode.status == "active",
            )
        )
        return bool(count)

    async def has_children(self, node_id: str) -> bool:
        count = await self.session.scalar(
            select(func.count()).select_from(LibraryTagNode).where(
                LibraryTagNode.parent_id == node_id
            )
        )
        return bool(count)

    async def has_assets(self, node_id: str) -> bool:
        count = await self.session.scalar(
            select(func.count()).select_from(LibraryAsset).where(
                LibraryAsset.leaf_tag_node_id == node_id
            )
        )
        return bool(count)

    async def delete_tag_node(self, node: LibraryTagNode) -> None:
        await self.session.delete(node)
        await self.session.commit()

    async def asset_counts(self) -> dict[str, int]:
        rows = await self.session.execute(
            select(LibraryAsset.leaf_tag_node_id, func.count(LibraryAsset.id)).group_by(
                LibraryAsset.leaf_tag_node_id
            )
        )
        return {node_id: count for node_id, count in rows}

    async def get_asset(self, asset_id: str) -> LibraryAsset | None:
        result = await self.session.execute(
            select(LibraryAsset)
            .options(selectinload(LibraryAsset.leaf_tag_node))
            .where(LibraryAsset.id == asset_id)
        )
        return result.scalar_one_or_none()

    async def get_asset_by_object_key(self, object_key: str) -> LibraryAsset | None:
        result = await self.session.execute(
            select(LibraryAsset).where(LibraryAsset.original_object_key == object_key)
        )
        return result.scalar_one_or_none()

    async def find_duplicate_asset(
        self, asset_id: str, sha256: str, phash: str
    ) -> LibraryAsset | None:
        result = await self.session.execute(
            select(LibraryAsset).where(
                LibraryAsset.id != asset_id,
                or_(LibraryAsset.sha256 == sha256, LibraryAsset.phash == phash),
                LibraryAsset.status.in_(("pending", "active", "disabled")),
            )
        )
        return result.scalars().first()

    async def create_asset(self, asset: LibraryAsset) -> LibraryAsset:
        self.session.add(asset)
        await self.session.commit()
        await self.session.refresh(asset)
        return asset

    async def update_asset(
        self, asset: LibraryAsset, values: Mapping[str, object]
    ) -> LibraryAsset:
        for name, value in values.items():
            setattr(asset, name, value)
        await self.session.commit()
        await self.session.refresh(asset)
        return asset

    async def delete_asset(self, asset: LibraryAsset) -> None:
        await self.session.delete(asset)
        await self.session.commit()

    async def list_assets(
        self, *, leaf_tag_node_id: str | None, status: str | None, limit: int, offset: int
    ) -> tuple[int, list[LibraryAsset]]:
        filters = []
        if leaf_tag_node_id:
            filters.append(LibraryAsset.leaf_tag_node_id == leaf_tag_node_id)
        if status:
            filters.append(LibraryAsset.status == status)
        total = await self.session.scalar(
            select(func.count()).select_from(LibraryAsset).where(*filters)
        )
        result = await self.session.execute(
            select(LibraryAsset)
            .options(selectinload(LibraryAsset.leaf_tag_node))
            .where(*filters)
            .order_by(LibraryAsset.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return int(total or 0), list(result.scalars())

    async def find_similar_assets(
        self, embedding: list[float], limit: int, scope_node_id: str | None = None
    ) -> list[tuple[LibraryAsset, float]]:
        root_filter = (
            LibraryTagNode.id == scope_node_id
            if scope_node_id
            else LibraryTagNode.parent_id.is_(None)
        )
        active_nodes = select(LibraryTagNode.id).where(
            root_filter,
            LibraryTagNode.status == "active",
        ).cte("active_library_tag_nodes", recursive=True)
        active_nodes = active_nodes.union_all(
            select(LibraryTagNode.id)
            .join(active_nodes, LibraryTagNode.parent_id == active_nodes.c.id)
            .where(LibraryTagNode.status == "active")
        )
        distance = LibraryAsset.embedding.cosine_distance(embedding)
        result = await self.session.execute(
            select(LibraryAsset, distance.label("distance"))
            .options(selectinload(LibraryAsset.leaf_tag_node))
            .where(
                LibraryAsset.status == "active",
                LibraryAsset.embedding.is_not(None),
                LibraryAsset.leaf_tag_node_id.in_(select(active_nodes.c.id)),
            )
            .order_by(distance)
            .limit(limit)
        )
        return [(asset, max(0.0, min(1.0, 1.0 - float(value)))) for asset, value in result]

    async def upsert_match(
        self, image_id: str, values: Mapping[str, object]
    ) -> ImageSimilarityMatch:
        result = await self.session.execute(
            select(ImageSimilarityMatch).where(ImageSimilarityMatch.image_id == image_id)
        )
        match = result.scalar_one_or_none()
        if match is None:
            match = ImageSimilarityMatch(id=f"sim_{image_id[4:]}", image_id=image_id, **values)
            self.session.add(match)
        else:
            for name, value in values.items():
                setattr(match, name, value)
        await self.session.commit()
        await self.session.refresh(match)
        return match

    async def get_match(self, image_id: str) -> ImageSimilarityMatch | None:
        result = await self.session.execute(
            select(ImageSimilarityMatch)
            .options(selectinload(ImageSimilarityMatch.matched_asset))
            .where(ImageSimilarityMatch.image_id == image_id)
        )
        return result.scalar_one_or_none()

    async def list_pending_reviews(self, limit: int, offset: int) -> list[ImageSimilarityMatch]:
        result = await self.session.execute(
            select(ImageSimilarityMatch)
            .options(selectinload(ImageSimilarityMatch.matched_asset))
            .where(ImageSimilarityMatch.decision == "pending_review")
            .order_by(ImageSimilarityMatch.created_at)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars())
