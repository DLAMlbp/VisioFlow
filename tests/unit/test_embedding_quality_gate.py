from types import SimpleNamespace

import pytest

from src.workers import matching


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


class _Embedder:
    def __init__(self, _settings) -> None:
        pass

    async def embed(self, image_bytes: bytes) -> list[float]:
        assert image_bytes in {b"thumbnail", b"delivery"}
        return [0.0] * 512


class _Storage:
    def __init__(self, expected_key: str) -> None:
        self.expected_key = expected_key

    async def download(self, object_key: str) -> bytes:
        assert object_key == self.expected_key
        return b"delivery" if object_key.startswith("analysis/") else b"thumbnail"


def _item(*, status: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="img_test",
        job_id="job_test",
        status=status,
        thumbnail_object_key="thumbnails/preprocessed.jpg",
        analysis_object_key=("analysis/delivery.jpg" if status == "enhanced" else None),
    )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        early_semantic_branch_enabled=True,
        library_image_only_matching_enabled=True,
        image_embedding_version="openclip-test",
    )


@pytest.mark.asyncio
async def test_pre_enhancement_embedding_is_provisional_and_cannot_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _item(status="enhancing")

    class Repository:
        completed: dict[str, object] | None = None

        def __init__(self, _session) -> None:
            pass

        async def claim_embedding(self, _image_id: str):
            return item

        async def get_config(self, _job_id: str):
            return SimpleNamespace(cancel_requested_at=None)

        async def complete_embedding_stage(self, _image_id: str, **values):
            type(self).completed = values

        async def get_item(self, _image_id: str):
            return item

        async def queue_final_embedding(self, _image_id: str) -> bool:
            raise AssertionError("enhancement has not completed")

        async def claim_match_if_ready(self, _image_id: str) -> bool:
            raise AssertionError("provisional vectors must never start final matching")

    published: list[str] = []
    monkeypatch.setattr(matching, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(matching, "ImageJobRepository", Repository)
    monkeypatch.setattr(matching, "get_settings", _settings)
    monkeypatch.setattr(matching, "load_ai_model_settings", lambda _settings: _settings)
    monkeypatch.setattr(matching, "OpenClipImageEmbedder", _Embedder)
    monkeypatch.setattr(
        matching, "get_storage_provider", lambda: _Storage("thumbnails/preprocessed.jpg")
    )
    monkeypatch.setattr(
        matching.MatchTaskPublisher,
        "publish",
        lambda _publisher, image_id: published.append(image_id),
    )

    await matching._generate_image_embedding(item.id)

    assert Repository.completed is not None
    assert Repository.completed["provisional"] is True
    assert published == []


@pytest.mark.asyncio
async def test_delivery_image_embedding_is_authoritative_and_starts_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _item(status="enhanced")

    class Repository:
        completed: dict[str, object] | None = None

        def __init__(self, _session) -> None:
            pass

        async def claim_embedding(self, _image_id: str):
            return item

        async def get_config(self, _job_id: str):
            return SimpleNamespace(cancel_requested_at=None)

        async def complete_embedding_stage(self, _image_id: str, **values):
            type(self).completed = values

        async def claim_match_if_ready(self, _image_id: str) -> bool:
            return True

    published: list[str] = []
    monkeypatch.setattr(matching, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(matching, "ImageJobRepository", Repository)
    monkeypatch.setattr(matching, "get_settings", _settings)
    monkeypatch.setattr(matching, "load_ai_model_settings", lambda _settings: _settings)
    monkeypatch.setattr(matching, "OpenClipImageEmbedder", _Embedder)
    monkeypatch.setattr(
        matching, "get_storage_provider", lambda: _Storage("analysis/delivery.jpg")
    )
    monkeypatch.setattr(
        matching.MatchTaskPublisher,
        "publish",
        lambda _publisher, image_id: published.append(image_id),
    )

    await matching._generate_image_embedding(item.id)

    assert Repository.completed is not None
    assert Repository.completed["provisional"] is False
    assert published == [item.id]


@pytest.mark.asyncio
async def test_provisional_completion_race_requeues_delivery_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claimed = _item(status="enhancing")
    enhanced = _item(status="enhanced")

    class Repository:
        def __init__(self, _session) -> None:
            pass

        async def claim_embedding(self, _image_id: str):
            return claimed

        async def get_config(self, _job_id: str):
            return SimpleNamespace(cancel_requested_at=None)

        async def complete_embedding_stage(self, _image_id: str, **values):
            assert values["provisional"] is True

        async def get_item(self, _image_id: str):
            return enhanced

        async def queue_final_embedding(self, _image_id: str) -> bool:
            return True

    published: list[str] = []
    monkeypatch.setattr(matching, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(matching, "ImageJobRepository", Repository)
    monkeypatch.setattr(matching, "get_settings", _settings)
    monkeypatch.setattr(matching, "load_ai_model_settings", lambda _settings: _settings)
    monkeypatch.setattr(matching, "OpenClipImageEmbedder", _Embedder)
    monkeypatch.setattr(
        matching, "get_storage_provider", lambda: _Storage("thumbnails/preprocessed.jpg")
    )
    monkeypatch.setattr(
        matching.EmbeddingTaskPublisher,
        "publish",
        lambda _publisher, image_id: published.append(image_id),
    )

    await matching._generate_image_embedding(claimed.id)

    assert published == [claimed.id]
