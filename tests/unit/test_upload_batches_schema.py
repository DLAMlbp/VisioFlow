import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.schemas.upload_batches import CreateUploadBatchRequest


def _files(count: int) -> list[dict[str, object]]:
    return [
        {
            "filename": f"image-{index}.jpg",
            "content_type": "image/jpeg",
            "file_size": 1024,
        }
        for index in range(count)
    ]


def test_upload_batch_accepts_500_images() -> None:
    payload = CreateUploadBatchRequest(
        filter_profile="flt_user", beautify_profile="bty_user", files=_files(500)
    )

    assert len(payload.files) == 500


def test_upload_batch_rejects_more_than_500_images() -> None:
    with pytest.raises(ValidationError):
        CreateUploadBatchRequest(
            filter_profile="flt_user", beautify_profile="bty_user", files=_files(501)
        )


def test_batch_pipeline_defaults_are_server_ready() -> None:
    settings = Settings(_env_file=None)

    assert settings.max_images_per_job == 500
    assert settings.job_dispatch_chunk_size == 25
    assert settings.image_retention_days == 30
    assert settings.inference_device == "auto"
