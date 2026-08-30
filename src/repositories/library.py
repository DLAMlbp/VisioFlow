from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.image_similarity_match import ImageSimilarityMatch
from src.models.library_asset import LibraryAsset
from src.models.library_asset_group import LibraryAssetGroup


class LibraryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_groups(self) -> list[LibraryAssetGroup]:
        result = await self.session.execute(
            select(LibraryAssetGroup).order_by(
                LibraryAssetGroup.sort_order, LibraryAssetGroup.created_at
            )
        )
        return list(result.scalars())

    async def get_group(self, group_id: str) -> LibraryAssetGroup | None:
        return await self.session.get(LibraryAssetGroup, group_id)

    async def find_group_by_tag_key(self, tag_key: str) -> LibraryAssetGroup | None:
        result = await self.session.execute(
            select(LibraryAssetGroup).where(LibraryAssetGroup.tag_key == tag_key)
        )
        return result.scalar_one_or_none()

    async def create_group(self, group: LibraryAssetGroup) -> LibraryAssetGroup:
        self.session.add(group)
        await self.session.commit()
        await self.session.refresh(group)
        return group

    async def update_group(
        self, group: LibraryAssetGroup, values: Mapping[str, object]
    ) -> LibraryAssetGroup:
        for name, value in values.items():
            setattr(group, name, value)
        await self.session.commit()
        await self.session.refresh(group)
        return group

    async def has_assets(self, group_id: str) -> bool:
        count = await self.session.scalar(
            select(func.count()).select_from(LibraryAsset).where(
                LibraryAsset.group_id == group_id
            )
        )
        return bool(count)

    async def delete_group(self, group: LibraryAssetGroup) -> None:
        await self.session.delete(group)
        await self.session.commit()

    async def group_asset_counts(self) -> dict[str, int]:
        rows = await self.session.execute(
            select(LibraryAsset.group_id, func.count(LibraryAsset.id)).group_by(
                LibraryAsset.group_id
            )
        )
        return {group_id: count for group_id, count in rows}

    async def get_asset(self, asset_id: str) -> LibraryAsset | None:
        result = await self.session.execute(
            select(LibraryAsset)
            .options(selectinload(LibraryAsset.group))
            .where(LibraryAsset.id == asset_id)
            .execution_options(populate_existing=True)
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
        self, *, group_id: str | None, status: str | None, limit: int, offset: int
    ) -> tuple[int, list[LibraryAsset]]:
        filters = []
        if group_id:
            filters.append(LibraryAsset.group_id == group_id)
        if status:
            filters.append(LibraryAsset.status == status)
        total = await self.session.scalar(
            select(func.count()).select_from(LibraryAsset).where(*filters)
        )
        result = await self.session.execute(
            select(LibraryAsset)
            .options(selectinload(LibraryAsset.group))
            .where(*filters)
            .order_by(LibraryAsset.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return int(total or 0), list(result.scalars())

    async def find_similar_assets(
        self, embedding: list[float], limit: int
    ) -> list[tuple[LibraryAsset, float]]:
        distance = LibraryAsset.embedding.cosine_distance(embedding)
        result = await self.session.execute(
            select(LibraryAsset, distance.label("distance"))
            .join(LibraryAsset.group)
            .options(selectinload(LibraryAsset.group))
            .where(
                LibraryAsset.status == "active",
                LibraryAsset.embedding.is_not(None),
                LibraryAssetGroup.status == "active",
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
