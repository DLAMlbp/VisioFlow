from fastapi.testclient import TestClient

from src.api.uploads import get_storage_provider
from src.core.config import Settings, get_settings
from src.main import app
from src.services.storage.interfaces import StorageProvider


class FakeStorageProvider(StorageProvider):
    async def upload(self, object_key: str, data: bytes, content_type: str) -> None:
        self.uploaded = (object_key, data, content_type)

    async def download(self, object_key: str) -> bytes:
        return f"download:{object_key}".encode()

    async def get_size(self, object_key: str) -> int:
        return len(f"download:{object_key}".encode())

    async def delete(self, object_key: str) -> None:
        self.deleted = object_key

    async def presign_upload(
        self,
        object_key: str,
        content_type: str,
        expires_seconds: int,
    ) -> str:
        return f"https://storage.example.test/upload/{object_key}?content_type={content_type}&ttl={expires_seconds}"

    async def presign_download(self, object_key: str, expires_seconds: int) -> str:
        return f"https://storage.example.test/download/{object_key}?ttl={expires_seconds}"


def test_presign_upload_returns_object_key_and_upload_url() -> None:
    app.dependency_overrides[get_storage_provider] = lambda: FakeStorageProvider()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/uploads/presign",
        json={
            "filename": "IMG_001.jpg",
            "content_type": "image/jpeg",
            "file_size": 4567281,
        },
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["object_key"].startswith("uploads/")
    assert body["object_key"].endswith(".jpg")
    assert body["upload_url"].startswith("https://storage.example.test/upload/uploads/")


def test_presign_upload_rejects_unsupported_mime() -> None:
    app.dependency_overrides[get_storage_provider] = lambda: FakeStorageProvider()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/uploads/presign",
        json={
            "filename": "IMG_001.gif",
            "content_type": "image/gif",
            "file_size": 1024,
        },
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "不支持" in response.json()["detail"]


def test_presign_download_returns_download_url() -> None:
    app.dependency_overrides[get_storage_provider] = lambda: FakeStorageProvider()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/uploads/presign-download",
        json={"object_key": "uploads/2026/08/19/example.jpg"},
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "object_key": "uploads/2026/08/19/example.jpg",
        "download_url": "https://storage.example.test/download/uploads/2026/08/19/example.jpg?ttl=900",
    }


def test_presign_download_rejects_invalid_object_key() -> None:
    app.dependency_overrides[get_storage_provider] = lambda: FakeStorageProvider()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/uploads/presign-download",
        json={"object_key": "uploads/../secret.jpg"},
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 400
