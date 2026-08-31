import pytest

from src.core.config import Settings
from src.schemas.integration import IntegrationUrlImage
from src.services import integration_urls
from src.services.integration_urls import (
    IntegrationUrlDownloadError,
    stage_integration_urls,
)
from src.services.storage.interfaces import StorageProvider


class FakeStorage(StorageProvider):
    def __init__(self) -> None:
        self.uploaded: list[tuple[str, bytes, str]] = []
        self.deleted: list[str] = []

    async def upload(self, object_key: str, data: bytes, content_type: str) -> None:
        self.uploaded.append((object_key, data, content_type))

    async def download(self, object_key: str) -> bytes:
        raise NotImplementedError

    async def get_size(self, object_key: str) -> int:
        raise NotImplementedError

    async def delete(self, object_key: str) -> None:
        self.deleted.append(object_key)

    async def presign_upload(self, object_key: str, content_type: str, expires_seconds: int) -> str:
        raise NotImplementedError

    async def presign_download(self, object_key: str, expires_seconds: int) -> str:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_stage_integration_urls_downloads_and_preserves_customer_keys(monkeypatch) -> None:
    def fake_download(url, **_kwargs):
        filename = url.rsplit("/", 1)[-1]
        return f"data:{filename}".encode(), "image/jpeg", filename

    monkeypatch.setattr(integration_urls, "_download_image", fake_download)
    storage = FakeStorage()
    images = [
        IntegrationUrlImage(objectKey="customer/a.jpg", imageUrl="https://obs.test/a.jpg"),
        IntegrationUrlImage(objectKey="customer/b.jpg", imageUrl="https://obs.test/b.jpg"),
    ]

    staged = await stage_integration_urls(
        images,
        storage=storage,
        settings=Settings(_env_file=None),
    )

    assert [item.client_object_key for item in staged] == ["customer/a.jpg", "customer/b.jpg"]
    assert len(storage.uploaded) == 2
    assert storage.deleted == []


@pytest.mark.asyncio
async def test_stage_integration_urls_removes_successful_uploads_if_any_download_fails(
    monkeypatch,
) -> None:
    def fake_download(url, **_kwargs):
        if url.endswith("bad.jpg"):
            raise IntegrationUrlDownloadError("download failed")
        return b"image", "image/jpeg", "good.jpg"

    monkeypatch.setattr(integration_urls, "_download_image", fake_download)
    storage = FakeStorage()
    images = [
        IntegrationUrlImage(objectKey="good", imageUrl="https://obs.test/good.jpg"),
        IntegrationUrlImage(objectKey="bad", imageUrl="https://obs.test/bad.jpg"),
    ]

    with pytest.raises(IntegrationUrlDownloadError, match="download failed"):
        await stage_integration_urls(
            images,
            storage=storage,
            settings=Settings(_env_file=None),
        )

    assert storage.deleted == [storage.uploaded[0][0]]


def test_url_download_blocks_loopback_and_private_addresses() -> None:
    with pytest.raises(IntegrationUrlDownloadError, match="本机或内网"):
        integration_urls._validate_public_http_url("http://127.0.0.1/image.jpg")
    with pytest.raises(IntegrationUrlDownloadError, match="本机或内网"):
        integration_urls._validate_public_http_url("http://169.254.169.254/latest/meta-data")
