from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import numpy as np
from redis import Redis
from redis.exceptions import RedisError

from src.core.config import Settings

logger = logging.getLogger(__name__)

_GENERATION_KEY = "image_intelligence:library_group_match_index:generation"


class PrototypeAsset(Protocol):
    id: str
    group_id: str
    embedding: Sequence[float] | None
    analysis_json: dict[str, object] | None
    sha256: str | None
    original_filename: str | None
    thumbnail_object_key: str | None
    original_object_key: str
    group: object


class PrototypeRepository(Protocol):
    async def list_group_prototype_assets(self) -> list[PrototypeAsset]: ...


@dataclass(frozen=True)
class CachedGroup:
    id: str
    tags: list[str]
    status: str


@dataclass(frozen=True)
class CachedPrototypeAsset:
    id: str
    group_id: str
    embedding: tuple[float, ...]
    analysis_json: dict[str, object] | None
    sha256: str | None
    original_filename: str | None
    thumbnail_object_key: str | None
    original_object_key: str
    group: CachedGroup


@dataclass(frozen=True)
class GroupPrototypeSnapshot:
    assets: tuple[CachedPrototypeAsset, ...]
    normalized_matrix: np.ndarray
    loaded_at: float
    generation: str | None

    @classmethod
    def build(
        cls,
        assets: Sequence[PrototypeAsset],
        *,
        generation: str | None,
        loaded_at: float | None = None,
    ) -> GroupPrototypeSnapshot:
        cached: list[CachedPrototypeAsset] = []
        vectors: list[list[float]] = []
        expected_dimension: int | None = None
        for asset in sorted(assets, key=lambda item: (item.group_id, item.id)):
            raw_embedding = list(asset.embedding or [])
            if not raw_embedding:
                continue
            vector = np.asarray(raw_embedding, dtype=np.float32)
            if vector.ndim != 1 or not np.all(np.isfinite(vector)):
                continue
            norm = float(np.linalg.norm(vector))
            if norm <= 0:
                continue
            if expected_dimension is None:
                expected_dimension = len(raw_embedding)
            if len(raw_embedding) != expected_dimension:
                logger.warning(
                    "Skipping group prototype with inconsistent embedding dimension asset_id=%s",
                    asset.id,
                )
                continue
            group = asset.group
            cached.append(
                CachedPrototypeAsset(
                    id=asset.id,
                    group_id=asset.group_id,
                    embedding=tuple(float(value) for value in raw_embedding),
                    analysis_json=(
                        dict(asset.analysis_json) if asset.analysis_json is not None else None
                    ),
                    sha256=asset.sha256,
                    original_filename=asset.original_filename,
                    thumbnail_object_key=asset.thumbnail_object_key,
                    original_object_key=asset.original_object_key,
                    group=CachedGroup(
                        id=str(group.id),
                        tags=list(group.tags),
                        status=str(group.status),
                    ),
                )
            )
            vectors.append((vector / norm).tolist())
        matrix = (
            np.asarray(vectors, dtype=np.float32)
            if vectors
            else np.empty((0, expected_dimension or 0), dtype=np.float32)
        )
        matrix.setflags(write=False)
        return cls(
            assets=tuple(cached),
            normalized_matrix=matrix,
            loaded_at=time.monotonic() if loaded_at is None else loaded_at,
            generation=generation,
        )

    def score(
        self, embedding: Sequence[float]
    ) -> list[tuple[CachedPrototypeAsset, float]]:
        query = np.asarray(list(embedding), dtype=np.float32)
        if (
            query.ndim != 1
            or not self.assets
            or self.normalized_matrix.shape[1] != query.shape[0]
            or not np.all(np.isfinite(query))
        ):
            return []
        norm = float(np.linalg.norm(query))
        if norm <= 0:
            return []
        scores = self.normalized_matrix @ (query / norm)
        return [
            (asset, max(0.0, min(1.0, float(score))))
            for asset, score in zip(self.assets, scores, strict=True)
        ]


class GroupPrototypeIndex:
    def __init__(self) -> None:
        self._snapshot: GroupPrototypeSnapshot | None = None
        self._observed_generation: str | None = None
        self._next_generation_check = 0.0

    async def find_matches(
        self,
        repository: PrototypeRepository,
        embedding: Sequence[float],
        settings: Settings,
    ) -> list[tuple[CachedPrototypeAsset, float]]:
        now = time.monotonic()
        generation = self._generation(settings, now)
        snapshot = self._snapshot
        expired = (
            snapshot is None
            or now - snapshot.loaded_at >= settings.library_group_index_max_age_seconds
        )
        generation_changed = (
            snapshot is not None
            and generation is not None
            and snapshot.generation != generation
        )
        if expired or generation_changed:
            assets = await repository.list_group_prototype_assets()
            snapshot = GroupPrototypeSnapshot.build(
                assets,
                generation=generation,
                loaded_at=now,
            )
            self._snapshot = snapshot
            logger.info(
                "Loaded group prototype index generation=%s prototypes=%d",
                generation,
                len(snapshot.assets),
            )
        return snapshot.score(embedding)

    def invalidate_local(self) -> None:
        self._snapshot = None
        self._next_generation_check = 0.0

    def _generation(self, settings: Settings, now: float) -> str | None:
        if now < self._next_generation_check:
            return self._observed_generation
        self._next_generation_check = (
            now + settings.library_group_index_version_check_seconds
        )
        try:
            self._observed_generation = (
                _redis(settings.redis_url).get(_GENERATION_KEY) or "0"
            )
        except RedisError:
            logger.warning(
                "Unable to read group prototype generation; using age-based refresh",
                exc_info=True,
            )
        return self._observed_generation


_group_prototype_index = GroupPrototypeIndex()


async def find_cached_group_prototype_matches(
    repository: PrototypeRepository,
    embedding: Sequence[float],
    settings: Settings,
) -> list[tuple[CachedPrototypeAsset, float]]:
    if not hasattr(repository, "list_group_prototype_assets"):
        legacy = repository.find_group_prototype_assets
        return await legacy(list(embedding))
    return await _group_prototype_index.find_matches(repository, embedding, settings)


def invalidate_group_prototype_index(settings: Settings) -> None:
    _group_prototype_index.invalidate_local()
    try:
        _redis(settings.redis_url).incr(_GENERATION_KEY)
    except RedisError:
        logger.warning(
            "Unable to publish group prototype generation; age-based refresh remains active",
            exc_info=True,
        )


@lru_cache(maxsize=4)
def _redis(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)
