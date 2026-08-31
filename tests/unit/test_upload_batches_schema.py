from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.schemas.upload_batches import CreateUploadBatchRequest
from src.services.managed_profiles import ManagedProfileService
from src.services.profiles import ProfileLoader
from src.services.upload_batches import UploadBatchService


def _files(count: int) -> list[dict[str, object]]:
    return [
        {
            "filename": f"image-{index}.jpg",
            "content_type": "image/jpeg",
            "file_size": 1024,
        }
        for index in range(count)
    ]


def _route() -> dict[str, str]:
    return {
        "completion_profile": "completion_renovation_v1",
        "completed_filter_profile": "standard_completed_v1",
        "non_completed_filter_profile": "standard_non_completed_v1",
    }


def test_upload_batch_accepts_500_images() -> None:
    payload = CreateUploadBatchRequest(
        filter_route=_route(),
        beautify_profile="bty_user",
        files=_files(500),
    )

    assert len(payload.files) == 500


def test_upload_batch_rejects_more_than_500_images() -> None:
    with pytest.raises(ValidationError):
        CreateUploadBatchRequest(
            filter_route=_route(),
            beautify_profile="bty_user",
            files=_files(501),
        )


def test_batch_pipeline_defaults_are_server_ready() -> None:
    settings = Settings(_env_file=None)

    assert settings.max_images_per_job == 500
    assert settings.job_dispatch_chunk_size == 25
    assert settings.image_retention_days == 30
    assert settings.inference_device == "auto"


def test_upload_batch_does_not_require_manual_standard_selection() -> None:
    payload = CreateUploadBatchRequest(
        beautify_profile="bty_user",
        files=_files(1),
    )

    assert payload.processing_standards == []


@pytest.mark.asyncio
async def test_upload_batch_freezes_all_active_standards_and_ignores_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []

    class Storage:
        async def presign_upload(self, object_key, _content_type, _expires):
            return f"https://storage.test/{object_key}"

    class Repository:
        async def create(self, batch):
            captured.append(batch)

    async def resolve_standards(_self, profile_ids=None, *, require_fallback=False):
        assert profile_ids is None
        assert require_fallback is True
        return [
            (SimpleNamespace(id="std_specific"), {"id": "std_specific"}),
            (SimpleNamespace(id="std_fallback"), {"id": "std_fallback"}),
        ]

    async def resolve_beautify(_self, profile_id):
        return SimpleNamespace(id=profile_id), {"id": profile_id, "config": {}}

    monkeypatch.setattr(
        "src.services.upload_batches.get_storage_provider", lambda: Storage()
    )
    monkeypatch.setattr(ManagedProfileService, "resolve_standards", resolve_standards)
    monkeypatch.setattr(ManagedProfileService, "resolve_beautify", resolve_beautify)
    monkeypatch.setattr(ProfileLoader, "get_similarity_profile", lambda *_args: object())
    service = UploadBatchService(SimpleNamespace(), Settings())
    service.repository = Repository()

    await service.create(
        CreateUploadBatchRequest(
            processing_standards=["requested-only"],
            beautify_profile="beautify",
            files=_files(1),
        )
    )

    assert len(captured) == 1
    assert captured[0].routing_mode == "streaming_v2"
    assert [item["id"] for item in captured[0].processing_standard_snapshots] == [
        "std_specific",
        "std_fallback",
    ]
