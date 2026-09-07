from types import SimpleNamespace

import pytest

from src.core.config import Settings
from src.services.images import group_match_index
from src.services.images.group_match_index import (
    GroupPrototypeIndex,
    GroupPrototypeSnapshot,
)


def _asset(asset_id: str, group_id: str, embedding: list[float]):
    return SimpleNamespace(
        id=asset_id,
        group_id=group_id,
        embedding=embedding,
        analysis_json={"scene": "室内"},
        sha256=None,
        original_filename=f"{asset_id}.jpg",
        thumbnail_object_key=None,
        original_object_key=f"library/{asset_id}.jpg",
        group=SimpleNamespace(id=group_id, tags=[group_id], status="active"),
    )


def test_group_prototype_snapshot_scores_every_group_without_database_ranking() -> None:
    snapshot = GroupPrototypeSnapshot.build(
        [
            _asset("a", "group_a", [1.0, 0.0]),
            _asset("b", "group_b", [0.0, 1.0]),
        ],
        generation="7",
        loaded_at=1.0,
    )

    matches = snapshot.score([1.0, 0.0])

    assert [(asset.id, score) for asset, score in matches] == [
        ("a", 1.0),
        ("b", 0.0),
    ]
    assert snapshot.normalized_matrix.flags.writeable is False


@pytest.mark.asyncio
async def test_group_prototype_index_reuses_snapshot_until_generation_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Repository:
        calls = 0

        async def list_group_prototype_assets(self):
            self.calls += 1
            return [_asset("a", "group_a", [1.0, 0.0])]

    class FakeRedis:
        generation = "1"

        def get(self, _key: str):
            return self.generation

    fake_redis = FakeRedis()
    monkeypatch.setattr(group_match_index, "_redis", lambda _url: fake_redis)
    settings = Settings(
        redis_url="redis://test",
        library_group_index_max_age_seconds=300,
        library_group_index_version_check_seconds=0.25,
    )
    repository = Repository()
    index = GroupPrototypeIndex()

    await index.find_matches(repository, [1.0, 0.0], settings)
    await index.find_matches(repository, [1.0, 0.0], settings)
    assert repository.calls == 1

    fake_redis.generation = "2"
    index._next_generation_check = 0.0
    await index.find_matches(repository, [1.0, 0.0], settings)
    assert repository.calls == 2
