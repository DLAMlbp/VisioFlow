import re

import pytest

from src.core.config import Settings
from src.core.exceptions import InvalidUploadRequest
from src.schemas.uploads import PresignedUploadRequest
from src.services.storage.keys import (
    build_enhanced_object_key,
    build_library_thumbnail_object_key,
    build_upload_object_key,
    validate_object_key,
    validate_upload_request,
)


def test_build_enhanced_object_key() -> None:
    assert build_enhanced_object_key("job_test", "img_test") == "enhanced/job_test/img_test.jpg"


def test_build_library_thumbnail_object_key() -> None:
    assert build_library_thumbnail_object_key("ast_test") == "library-thumbnails/ast_test.jpg"


def make_settings() -> Settings:
    return Settings(max_image_size_mb=25)


def test_validate_upload_request_accepts_jpeg_png_and_webp() -> None:
    settings = make_settings()

    for filename, content_type in [
        ("photo.jpg", "image/jpeg"),
        ("photo.jpeg", "image/jpeg"),
        ("photo.png", "image/png"),
        ("photo.webp", "image/webp"),
    ]:
        validate_upload_request(
            PresignedUploadRequest(
                filename=filename,
                content_type=content_type,
                file_size=1024,
            ),
            settings=settings,
        )


def test_validate_upload_request_rejects_unsupported_content_type() -> None:
    with pytest.raises(InvalidUploadRequest, match="不支持"):
        validate_upload_request(
            PresignedUploadRequest(
                filename="photo.gif",
                content_type="image/gif",
                file_size=1024,
            ),
            settings=make_settings(),
        )


def test_validate_upload_request_rejects_file_too_large() -> None:
    with pytest.raises(InvalidUploadRequest, match="文件大小"):
        validate_upload_request(
            PresignedUploadRequest(
                filename="photo.jpg",
                content_type="image/jpeg",
                file_size=26 * 1024 * 1024,
            ),
            settings=make_settings(),
        )


def test_validate_upload_request_rejects_suffix_mismatch() -> None:
    with pytest.raises(InvalidUploadRequest, match="扩展名"):
        validate_upload_request(
            PresignedUploadRequest(
                filename="photo.png",
                content_type="image/jpeg",
                file_size=1024,
            ),
            settings=make_settings(),
        )


def test_build_upload_object_key_uses_uploads_date_uuid_and_extension() -> None:
    object_key = build_upload_object_key("IMG_001.jpeg", "image/jpeg")

    assert re.match(r"^uploads/\d{4}/\d{2}/\d{2}/[a-f0-9]{32}\.jpg$", object_key)


def test_validate_object_key_rejects_path_traversal() -> None:
    with pytest.raises(InvalidUploadRequest):
        validate_object_key("uploads/../secret.jpg")
