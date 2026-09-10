from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import case, func, select, update
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

    async def find_reusable_exact_asset(
        self, asset_id: str, sha256: str
    ) -> LibraryAsset | None:
        result = await self.session.execute(
            select(LibraryAsset).where(
                LibraryAsset.id != asset_id,
                LibraryAsset.sha256 == sha256,
                LibraryAsset.status.in_(("active", "disabled")),
                LibraryAsset.analysis_json.is_not(None),
                LibraryAsset.embedding.is_not(None),
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

    async def reset_failed_assets(self, *, group_id: str | None) -> list[tuple[str, str]]:
        filters = [LibraryAsset.status == "failed"]
        if group_id:
            filters.append(LibraryAsset.group_id == group_id)
        result = await self.session.execute(
            update(LibraryAsset)
            .where(*filters)
            .values(
                status="pending",
                error_message=None,
                analysis_json=None,
                embedding=None,
                embedding_version=None,
                is_group_prototype=False,
            )
            .returning(LibraryAsset.id, LibraryAsset.group_id)
        )
        rows = [(str(asset_id), str(asset_group_id)) for asset_id, asset_group_id in result]
        await self.session.commit()
        return rows

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

    async def find_group_prototype_assets(
        self, embedding: list[float]
    ) -> list[tuple[LibraryAsset, float]]:
        """Return the small, precomputed representative set for every active group."""
        distance = LibraryAsset.embedding.cosine_distance(embedding)
        result = await self.session.execute(
            select(LibraryAsset, distance.label("distance"))
            .join(LibraryAsset.group)
            .options(selectinload(LibraryAsset.group))
            .where(
                LibraryAsset.status == "active",
                LibraryAsset.is_group_prototype.is_(True),
                LibraryAsset.embedding.is_not(None),
                LibraryAssetGroup.status == "active",
            )
            .order_by(LibraryAsset.group_id, distance)
        )
        return [
            (asset, max(0.0, min(1.0, 1.0 - float(value))))
            for asset, value in result
        ]

    async def list_group_prototype_assets(self) -> list[LibraryAsset]:
        """Load the small representative set used to build a process-local index."""
        result = await self.session.execute(
            select(LibraryAsset)
            .join(LibraryAsset.group)
            .options(selectinload(LibraryAsset.group))
            .where(
                LibraryAsset.status == "active",
                LibraryAsset.is_group_prototype.is_(True),
                LibraryAsset.embedding.is_not(None),
                LibraryAssetGroup.status == "active",
            )
            .order_by(LibraryAsset.group_id, LibraryAsset.id)
        )
        return list(result.scalars())

    async def list_active_group_assets_with_embeddings(
        self, group_id: str
    ) -> list[LibraryAsset]:
        result = await self.session.execute(
            select(LibraryAsset)
            .where(
                LibraryAsset.group_id == group_id,
                LibraryAsset.status == "active",
                LibraryAsset.embedding.is_not(None),
            )
            .order_by(LibraryAsset.id)
        )
        return list(result.scalars())

    async def replace_group_prototypes(
        self, group_id: str, asset_ids: list[str]
    ) -> None:
        await self.session.execute(
            update(LibraryAsset)
            .where(LibraryAsset.group_id == group_id)
            .values(is_group_prototype=False)
        )
        if asset_ids:
            await self.session.execute(
                update(LibraryAsset)
                .where(
                    LibraryAsset.group_id == group_id,
                    LibraryAsset.id.in_(asset_ids),
                )
                .values(is_group_prototype=True)
            )
        await self.session.execute(
            update(LibraryAssetGroup)
            .where(LibraryAssetGroup.id == group_id)
            .values(prototype_version=1)
        )
        await self.session.commit()

    async def mark_group_prototypes_stale(self, group_ids: list[str]) -> None:
        unique_ids = sorted(set(group_ids))
        if not unique_ids:
            return
        await self.session.execute(
            update(LibraryAssetGroup)
            .where(LibraryAssetGroup.id.in_(unique_ids))
            .values(prototype_version=0)
        )
        await self.session.commit()

    async def list_groups_needing_prototype_refresh(
        self, *, max_prototypes: int, limit: int
    ) -> list[str]:
        prototype_count = func.sum(
            case((LibraryAsset.is_group_prototype.is_(True), 1), else_=0)
        )
        result = await self.session.execute(
            select(LibraryAsset.group_id)
            .join(LibraryAsset.group)
            .where(
                LibraryAsset.status == "active",
                LibraryAsset.embedding.is_not(None),
                LibraryAssetGroup.status == "active",
            )
            .group_by(LibraryAsset.group_id)
            .having(
                (func.max(LibraryAssetGroup.prototype_version) < 1)
                | (prototype_count == 0)
                | (prototype_count > max_prototypes)
            )
            .order_by(LibraryAsset.group_id)
            .limit(limit)
        )
        return list(result.scalars())

    async def find_exact_active_assets(self, sha256: str) -> list[LibraryAsset]:
        result = await self.session.execute(
            select(LibraryAsset)
            .join(LibraryAsset.group)
            .options(selectinload(LibraryAsset.group))
            .where(
                LibraryAsset.sha256 == sha256,
                LibraryAsset.status == "active",
                LibraryAssetGroup.status == "active",
            )
            .order_by(LibraryAsset.created_at)
        )
        return list(result.scalars())

    async def list_active_assets_missing_analysis(self, *, limit: int) -> list[str]:
        result = await self.session.execute(
            select(LibraryAsset.id)
            .where(
                LibraryAsset.status == "active",
                LibraryAsset.embedding.is_not(None),
                LibraryAsset.analysis_json.is_(None),
            )
            .order_by(LibraryAsset.created_at)
            .limit(limit)
        )
        return list(result.scalars())

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
