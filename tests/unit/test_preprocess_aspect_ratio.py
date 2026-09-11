from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from src.core.config import Settings
from src.services.profiles import BeautifyProfile
from src.workers import preprocess


def _jpeg(size: tuple[int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "#8a7460").save(output, format="JPEG", quality=95)
    return output.getvalue()


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class _Storage:
    def __init__(self, object_key: str, data: bytes) -> None:
        self.objects = {object_key: data}

    async def get_size(self, object_key: str) -> int:
        return len(self.objects[object_key])

    async def download(self, object_key: str) -> bytes:
        return self.objects[object_key]

    async def upload(self, object_key: str, data: bytes, _content_type: str) -> None:
        self.objects[object_key] = data


class _Repository:
    def __init__(self, item, job) -> None:
        self.item = item
        self.job = job
        self.metadata: dict[str, object] | None = None
        self.metrics: dict[str, object] | None = None

    async def get_item(self, _image_id: str):
        return self.item

    async def claim_preprocess(self, _image_id: str):
        return self.item

    async def get_config(self, _job_id: str):
        return self.job

    async def save_metadata_and_find_duplicates(self, item, values, **_kwargs):
        self.metadata = dict(values)
        for key, value in values.items():
            setattr(item, key, value)
        return None, None

    async def upsert_metrics(self, _image_id: str, values) -> None:
        self.metrics = dict(values)

    async def complete_metadata_for_completion(self, _item) -> bool:
        return True


@pytest.mark.asyncio
async def test_preprocess_stores_portrait_source_before_filter_dispatch(monkeypatch) -> None:
    source_key = "uploads/source.jpg"
    source_bytes = _jpeg((1600, 900))
    storage = _Storage(source_key, source_bytes)
    item = SimpleNamespace(
        id="img_1",
        job_id="job_1",
        object_key=source_key,
        status="queued",
    )
    job = SimpleNamespace(
        filter_enabled=False,
        processing_standard_snapshots=[],
        filter_profile_snapshot=None,
        filter_profile_id="filter",
        beautify_profile_snapshot=None,
        beautify_profile_id="beautify",
        redaction_profile_snapshot=None,
        routing_mode="completion",
    )
    repository = _Repository(item, job)
    settings = Settings(_env_file=None)
    profile = BeautifyProfile(
        id="beautify",
        version=1,
        description="test",
        brightness=1,
        contrast=1,
        color=1,
        sharpness=1,
        auto_straighten=False,
        jpeg_quality=95,
    )
    published: list[str] = []

    monkeypatch.setattr(preprocess, "AsyncSessionLocal", _Session)
    monkeypatch.setattr(preprocess, "ImageJobRepository", lambda _session: repository)
    monkeypatch.setattr(preprocess, "get_settings", lambda: settings)
    monkeypatch.setattr(preprocess, "load_ai_model_settings", lambda value: value)
    monkeypatch.setattr(preprocess, "get_storage_provider", lambda: storage)
    monkeypatch.setattr(preprocess, "standards_from_snapshots", lambda _value: [])
    monkeypatch.setattr(preprocess, "beautify_from_snapshot", lambda *_args: profile)
    monkeypatch.setattr(
        preprocess,
        "redaction_from_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        preprocess,
        "CompletionTaskPublisher",
        lambda: SimpleNamespace(publish=published.append),
    )

    await preprocess._preprocess_image_metadata(item.id)

    processing_key = "processing-sources/job_1/img_1.jpg"
    thumbnail_key = "thumbnails/job_1/img_1.jpg"
    assert storage.objects[source_key] == source_bytes
    with Image.open(BytesIO(storage.objects[processing_key])) as processed:
        assert processed.size == (675, 900)
    with Image.open(BytesIO(storage.objects[thumbnail_key])) as thumbnail:
        assert thumbnail.size == (576, 768)
    assert repository.metadata is not None
    assert repository.metadata["processing_object_key"] == processing_key
    assert repository.metadata["width"] == 675
    assert repository.metadata["height"] == 900
    assert repository.metadata["aspect_ratio"] == 0.75
    assert repository.metadata["normalization_json"] == {
        "version": 1,
        "mode": "center_crop",
        "target_ratio": "3:4",
        "uploaded_size": [1600, 900],
        "input_size": [1600, 900],
        "crop_box": [462, 0, 1137, 900],
        "processed_size": [675, 900],
        "retained_area_ratio": 0.4219,
        "applied": True,
    }
    assert repository.metrics is not None
    assert published == [item.id]
