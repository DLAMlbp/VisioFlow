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
        object_key="uploads/source.jpg",
        sha256="same-sha",
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
async def test_final_embedding_uses_stable_preprocessed_source_and_starts_match(
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
        matching, "get_storage_provider", lambda: _Storage("thumbnails/preprocessed.jpg")
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


@pytest.mark.asyncio
async def test_embedding_reuses_same_sha_and_version_without_downloading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _item(status="enhanced")
    cached = [0.25] * 512

    class Repository:
        completed: dict[str, object] | None = None

        def __init__(self, _session) -> None:
            pass

        async def claim_embedding(self, _image_id: str):
            return item

        async def get_config(self, _job_id: str):
            return SimpleNamespace(cancel_requested_at=None)

        async def find_reusable_image_embedding(self, **values):
            assert values["sha256"] == "same-sha"
            assert values["embedding_version"] == "openclip-test+source_v2"
            return cached

        async def complete_embedding_stage(self, _image_id: str, **values):
            type(self).completed = values

        async def claim_match_if_ready(self, _image_id: str) -> bool:
            return True

    class Storage:
        async def download(self, _object_key: str) -> bytes:
            raise AssertionError("cached embedding must avoid storage download")

    monkeypatch.setattr(matching, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(matching, "ImageJobRepository", Repository)
    monkeypatch.setattr(matching, "get_settings", _settings)
    monkeypatch.setattr(matching, "load_ai_model_settings", lambda _settings: _settings)
    monkeypatch.setattr(matching, "get_storage_provider", lambda: Storage())
    monkeypatch.setattr(matching.MatchTaskPublisher, "publish", lambda *_args: None)

    await matching._generate_image_embedding(item.id)

    assert Repository.completed is not None
    assert Repository.completed["embedding"] == cached


@pytest.mark.asyncio
async def test_exact_active_library_sha_scores_one_and_writes_automatic_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ai_tag = SimpleNamespace(
        tag_json={"summary": "开工大吉", "content_confidence": 0.9},
        source_object_key="thumbnails/preprocessed.jpg",
        provider="openai",
        model_name="vision-test",
        prompt_version="generic_visual_analysis_v2",
        status="completed",
        duration_ms=20,
        raw_response_json=None,
    )
    item = SimpleNamespace(
        id="img_test",
        job_id="job_test",
        sha256="same-sha",
        embedding_status="completed",
        embedding=[0.25] * 512,
        embedding_version="openclip-test+source_v2",
        analysis_status="completed",
        ai_tag=ai_tag,
    )
    asset = SimpleNamespace(
        id="ast_exact",
        group_id="grp_exact",
        group=SimpleNamespace(tags=["开工大吉"]),
        sha256="same-sha",
        original_filename="exact.jpg",
        thumbnail_object_key="library-thumbnails/exact.jpg",
        original_object_key="library/exact.jpg",
    )

    class Repository:
        saved_tag: dict[str, object] | None = None
        completed = False

        def __init__(self, _session) -> None:
            pass

        async def claim_match(self, _image_id: str):
            return item

        async def get_config(self, _job_id: str):
            return SimpleNamespace(
                cancel_requested_at=None,
                similarity_profile_id="library_similarity_v2",
            )

        async def upsert_ai_tag(self, **values):
            type(self).saved_tag = values

        async def complete_match_stage(self, _image_id: str):
            type(self).completed = True

        async def complete_tagging(self, _item, *, reason: str):
            assert "匹配" in reason or "相同" in reason

    class LibraryRepository:
        saved_match: dict[str, object] | None = None

        def __init__(self, _session) -> None:
            pass

        async def find_exact_active_assets(self, sha256: str):
            assert sha256 == "same-sha"
            return [asset]

        async def find_similar_assets(self, *_args):
            raise AssertionError("exact SHA must bypass vector candidate retrieval")

        async def upsert_match(self, *, image_id: str, values):
            assert image_id == "img_test"
            type(self).saved_match = values

    workflow_settings = SimpleNamespace(
        early_semantic_branch_enabled=False,
        library_image_only_matching_enabled=True,
        library_only_tags_enabled=True,
        profiles_directory="profiles",
    )
    profile = SimpleNamespace(
        similarity_auto_threshold=0.60,
        similarity_review_threshold=0.45,
    )

    monkeypatch.setattr(matching, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(matching, "ImageJobRepository", Repository)
    monkeypatch.setattr(matching, "LibraryRepository", LibraryRepository)
    monkeypatch.setattr(matching, "get_settings", lambda: workflow_settings)
    monkeypatch.setattr(
        matching, "load_ai_model_settings", lambda _settings: workflow_settings
    )
    monkeypatch.setattr(
        matching.ProfileLoader,
        "get_similarity_profile",
        lambda _loader, _profile_id: profile,
    )

    await matching._match_image_library("img_test")

    assert LibraryRepository.saved_match is not None
    assert LibraryRepository.saved_match["decision"] == "matched"
    assert LibraryRepository.saved_match["similarity_score"] == 1.0
    assert LibraryRepository.saved_match["final_score"] == 1.0
    assert LibraryRepository.saved_match["feature_coverage"] == 1.0
    assert LibraryRepository.saved_match["matched_tags_snapshot"] == ["开工大吉"]
    assert Repository.saved_tag is not None
    assert Repository.saved_tag["provider"] == "library"
    assert Repository.saved_tag["model_name"] == "openclip-test+source_v2"
    assert Repository.saved_tag["prompt_version"] == "library_similarity_v2"
    assert Repository.saved_tag["tag_json"]["tags"] == ["开工大吉"]
    assert Repository.saved_tag["tag_json"]["content_analysis_provenance"] == {
        "provider": "openai",
        "model_name": "vision-test",
        "prompt_version": "generic_visual_analysis_v2",
    }
    assert Repository.saved_tag["tag_json"]["confidence"] == 1.0
    assert Repository.completed is True
