from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.core.config import Settings
from src.core.exceptions import InvalidUploadRequest
from src.schemas.uploads import PresignedUploadRequest

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def validate_upload_request(payload: PresignedUploadRequest, settings: Settings) -> None:
    if payload.content_type not in settings.allowed_image_content_types:
        raise InvalidUploadRequest("不支持的图片类型")

    max_bytes = settings.max_image_size_mb * 1024 * 1024
    if payload.file_size > max_bytes:
        raise InvalidUploadRequest(f"文件大小不能超过 {settings.max_image_size_mb}MB")

    suffix = Path(payload.filename).suffix.lower()
    expected_suffix = CONTENT_TYPE_EXTENSIONS.get(payload.content_type)
    jpeg_aliases = {".jpg", ".jpeg"}

    if payload.content_type == "image/jpeg":
        valid_suffix = suffix in jpeg_aliases
    else:
        valid_suffix = suffix == expected_suffix

    if suffix and not valid_suffix:
        raise InvalidUploadRequest("文件扩展名与 Content-Type 不匹配")


def build_upload_object_key(filename: str, content_type: str) -> str:
    now = datetime.now(UTC)
    suffix = Path(filename).suffix.lower() or CONTENT_TYPE_EXTENSIONS.get(content_type, ".bin")
    if suffix == ".jpeg":
        suffix = ".jpg"
    return f"uploads/{now:%Y/%m/%d}/{uuid4().hex}{suffix}"


def build_thumbnail_object_key(job_id: str, image_id: str) -> str:
    return f"thumbnails/{job_id}/{image_id}.jpg"


def build_enhanced_object_key(job_id: str, image_id: str) -> str:
    return f"enhanced/{job_id}/{image_id}.jpg"


def build_analysis_object_key(job_id: str, image_id: str) -> str:
    return f"analysis/{job_id}/{image_id}.jpg"


def validate_object_key(object_key: str) -> None:
    if object_key.startswith("/") or ".." in object_key.split("/"):
        raise InvalidUploadRequest("object_key 不合法")
