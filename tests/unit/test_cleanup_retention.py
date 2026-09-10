from types import SimpleNamespace

import pytest

from src.core.config import Settings
from src.services.storage.interfaces import StorageProvider
from src.services.storage.minio import MinIOStorageProvider
from src.workers.cleanup import _deletable_keys_by_item, _task_object_keys


class RecordingStorage(StorageProvider):
    def __init__(self, failing: set[str] | None = None) -> None:
        self.deleted: list[str] = []
        self.failing = failing or set()

    async def healthcheck(self) -> None:
        pass

    async def upload(self, object_key: str, data: bytes, content_type: str) -> None:
        pass

    async def download(self, object_key: str) -> bytes:
        return b""

    async def get_size(self, object_key: str) -> int:
        return 0

    async def delete(self, object_key: str) -> None:
        self.deleted.append(object_key)
        if object_key in self.failing:
            raise RuntimeError("delete failed")

    async def presign_upload(
        self,
        object_key: str,
        content_type: str,
        expires_seconds: int,
    ) -> str:
        return object_key

    async def presign_download(self, object_key: str, expires_seconds: int) -> str:
        return object_key


def test_task_object_keys_include_all_task_artifacts() -> None:
    item = SimpleNamespace(
        id="img_1",
        job_id="job_1",
        object_key="uploads/original.jpg",
        processing_object_key="processing-sources/job_1/img_1.jpg",
        thumbnail_object_key="thumbnails/job_1/img_1.jpg",
        analysis_object_key="analysis/job_1/img_1.jpg",
        result=SimpleNamespace(enhanced_object_key="enhanced/job_1/img_1.jpg"),
        ai_tag=SimpleNamespace(source_object_key="library-thumbnails/asset_1.jpg"),
    )

    keys = _task_object_keys(item)

    assert "uploads/original.jpg" in keys
    assert "processing-sources/job_1/img_1.jpg" in keys
    assert "thumbnails/job_1/img_1.jpg" in keys
    assert "analysis/job_1/img_1.jpg" in keys
    assert "enhanced/job_1/img_1.jpg" in keys
    assert "redaction-bases/job_1/img_1.jpg" in keys
    assert "enhancement-work/job_1/img_1/state.json" in keys
    assert "library-thumbnails/asset_1.jpg" in keys


def test_deletable_keys_never_include_library_objects() -> None:
    item = SimpleNamespace(
        id="img_1",
        job_id="job_1",
        object_key="uploads/library-original.jpg",
        thumbnail_object_key="thumbnails/job_1/img_1.jpg",
        analysis_object_key=None,
        result=SimpleNamespace(enhanced_object_key="enhanced/job_1/img_1.jpg"),
        ai_tag=SimpleNamespace(source_object_key="library-thumbnails/asset_1.jpg"),
    )

    keys = _deletable_keys_by_item(
        [item],
        {"uploads/library-original.jpg", "library-thumbnails/asset_1.jpg"},
    )

    assert "uploads/library-original.jpg" not in keys["img_1"]
    assert "library-thumbnails/asset_1.jpg" not in keys["img_1"]
    assert "thumbnails/job_1/img_1.jpg" in keys["img_1"]
    assert "enhanced/job_1/img_1.jpg" in keys["img_1"]


@pytest.mark.asyncio
async def test_storage_delete_many_reports_failures() -> None:
    storage = RecordingStorage(failing={"uploads/failed.jpg"})

    failed = await storage.delete_many(["uploads/ok.jpg", "uploads/failed.jpg", "uploads/ok.jpg"])

    assert storage.deleted == ["uploads/ok.jpg", "uploads/failed.jpg"]
    assert failed == {"uploads/failed.jpg"}


@pytest.mark.asyncio
async def test_minio_delete_many_chunks_requests_and_returns_errors() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def delete_objects(self, *, Bucket: str, Delete: dict[str, object]):
            assert Bucket == "image-ai"
            keys = [item["Key"] for item in Delete["Objects"]]
            self.calls.append(keys)
            return {"Errors": [{"Key": "uploads/1000.jpg"}]} if len(self.calls) == 2 else {}

    client = Client()
    storage = MinIOStorageProvider(Settings(_env_file=None))
    storage.__dict__["client"] = client

    failed = await storage.delete_many([f"uploads/{index}.jpg" for index in range(1001)])

    assert [len(call) for call in client.calls] == [1000, 1]
    assert failed == {"uploads/1000.jpg"}
