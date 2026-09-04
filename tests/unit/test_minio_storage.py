from unittest.mock import Mock

import pytest

from src.core.config import Settings
from src.services.storage.minio import MinIOStorageProvider


def test_storage_client_has_bounded_timeouts_and_retry_budget() -> None:
    provider = MinIOStorageProvider(Settings(_env_file=None))

    config = provider.client.meta.config

    assert config.connect_timeout == 3
    assert config.read_timeout == 20
    assert config.retries["total_max_attempts"] == 2
    assert config.retries["mode"] == "standard"
    assert config.tcp_keepalive is True


@pytest.mark.asyncio
async def test_presign_download_requests_an_attachment_response() -> None:
    provider = MinIOStorageProvider(Settings())
    public_client = Mock()
    public_client.generate_presigned_url.return_value = "https://storage.test/presigned"
    provider.__dict__["public_client"] = public_client

    url = await provider.presign_download("enhanced/job-1/result image.jpg", 900)

    assert url == "https://storage.test/presigned"
    public_client.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={
            "Bucket": provider.settings.s3_bucket,
            "Key": "enhanced/job-1/result image.jpg",
            "ResponseContentDisposition": (
                "attachment; filename*=UTF-8''result%20image.jpg"
            ),
        },
        ExpiresIn=900,
    )
