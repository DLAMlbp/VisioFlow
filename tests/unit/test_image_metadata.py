from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image

from src.core.config import Settings
from src.services.images.metadata import (
    ImageMetadataError,
    ImageMetadataService,
    build_perceptual_hash,
    detect_image_content_type,
)
from src.services.storage.interfaces import StorageProvider


class MemoryStorage(StorageProvider):
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.uploads: dict[str, tuple[bytes, str]] = {}

    async def upload(self, object_key: str, data: bytes, content_type: str) -> None:
        self.uploads[object_key] = (data, content_type)

    async def download(self, object_key: str) -> bytes:
        return self.objects[object_key]

    async def get_size(self, object_key: str) -> int:
        return len(self.objects[object_key])

    async def delete(self, object_key: str) -> None:
        self.objects.pop(object_key, None)

    async def presign_upload(self, object_key: str, content_type: str, expires_seconds: int) -> str:
        return object_key

    async def presign_download(self, object_key: str, expires_seconds: int) -> str:
        return object_key


def image_bytes(image_format: str, size: tuple[int, int] = (1600, 800)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "red").save(output, format=image_format)
    return output.getvalue()


@pytest.mark.parametrize(
    ("image_format", "content_type"),
    [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")],
)
def test_detect_image_content_type(image_format: str, content_type: str) -> None:
    assert detect_image_content_type(image_bytes(image_format)) == content_type


@pytest.mark.asyncio
async def test_process_extracts_metadata_and_uploads_thumbnail() -> None:
    source = image_bytes("PNG")
    storage = MemoryStorage({"uploads/source.png": source})
    service = ImageMetadataService(storage, Settings(thumbnail_long_side=768))

    metadata = await service.process(
        job_id="job_test",
        image_id="img_test",
        object_key="uploads/source.png",
    )

    assert metadata.content_type == "image/png"
    assert metadata.file_size == len(source)
    assert (metadata.width, metadata.height) == (1600, 800)
    assert metadata.aspect_ratio == 2.0
    assert metadata.orientation is None
    assert metadata.sha256 == sha256(source).hexdigest()
    assert len(metadata.phash) == 64
    assert metadata.thumbnail_object_key == "thumbnails/job_test/img_test.jpg"
    assert metadata.processing_object_key is None
    assert metadata.downscaled is False
    assert metadata.processing_bytes == source
    thumbnail_bytes, content_type = storage.uploads[metadata.thumbnail_object_key]
    assert content_type == "image/jpeg"
    with Image.open(BytesIO(thumbnail_bytes)) as thumbnail:
        assert thumbnail.size == (768, 384)
        assert thumbnail.format == "JPEG"


@pytest.mark.asyncio
async def test_process_uses_exif_orientation_for_thumbnail() -> None:
    output = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (1600, 800), "red").save(output, format="JPEG", exif=exif)
    storage = MemoryStorage({"uploads/rotated.jpg": output.getvalue()})
    service = ImageMetadataService(storage, Settings(thumbnail_long_side=768))

    metadata = await service.process(
        job_id="job_test",
        image_id="img_rotated",
        object_key="uploads/rotated.jpg",
    )

    assert metadata.orientation == 6
    thumbnail_bytes, _ = storage.uploads[metadata.thumbnail_object_key]
    with Image.open(BytesIO(thumbnail_bytes)) as thumbnail:
        assert thumbnail.size == (384, 768)


@pytest.mark.asyncio
async def test_process_rejects_unsupported_or_corrupted_file() -> None:
    storage = MemoryStorage({"uploads/bad.gif": b"GIF89a not a valid permitted image"})
    service = ImageMetadataService(storage, Settings())

    with pytest.raises(ImageMetadataError, match="不支持") as error:
        await service.process(job_id="job_test", image_id="img_test", object_key="uploads/bad.gif")
    assert error.value.reject_code == "INVALID_IMAGE"


@pytest.mark.asyncio
async def test_process_rejects_object_larger_than_limit_before_download() -> None:
    storage = MemoryStorage({"uploads/large.jpg": b"x" * 1024})
    service = ImageMetadataService(storage, Settings(max_image_size_mb=0))

    with pytest.raises(ImageMetadataError, match="实际大小") as error:
        await service.process(job_id="job_test", image_id="img_test", object_key="uploads/large.jpg")
    assert error.value.reject_code == "IMAGE_TOO_LARGE"


@pytest.mark.asyncio
async def test_process_downscales_decoded_pixels_above_processing_budget() -> None:
    source = image_bytes("JPEG", (1200, 1000))
    storage = MemoryStorage({"uploads/too-many-pixels.jpg": source})
    service = ImageMetadataService(
        storage,
        Settings(max_image_pixels=1_000_000, max_image_decode_pixels=2_000_000),
    )

    metadata = await service.process(
        job_id="job_test",
        image_id="img_test",
        object_key="uploads/too-many-pixels.jpg",
    )

    assert metadata.downscaled is True
    assert metadata.processing_width * metadata.processing_height <= 1_000_000
    assert metadata.processing_object_key == "processing-sources/job_test/img_test.jpg"
    processing_bytes, content_type = storage.uploads[metadata.processing_object_key]
    assert content_type == "image/jpeg"
    assert processing_bytes == metadata.processing_bytes
    assert metadata.original_bytes == source
    with Image.open(BytesIO(processing_bytes)) as processing_image:
        assert processing_image.size == (
            metadata.processing_width,
            metadata.processing_height,
        )


@pytest.mark.asyncio
async def test_process_accepts_and_downscales_common_4032_by_3024_photo() -> None:
    source = image_bytes("JPEG", (4032, 3024))
    storage = MemoryStorage({"uploads/phone-photo.jpg": source})

    metadata = await ImageMetadataService(storage, Settings()).process(
        job_id="job_phone",
        image_id="img_phone",
        object_key="uploads/phone-photo.jpg",
    )

    assert metadata.downscaled is True
    assert (metadata.processing_width, metadata.processing_height) == (4000, 3000)
    assert metadata.processing_width * metadata.processing_height == 12_000_000
    assert metadata.processing_object_key == "processing-sources/job_phone/img_phone.jpg"


@pytest.mark.asyncio
async def test_process_rejects_decoded_pixels_above_hard_safety_limit() -> None:
    source = image_bytes("JPEG", (1200, 1000))
    storage = MemoryStorage({"uploads/unsafe.jpg": source})
    service = ImageMetadataService(
        storage,
        Settings(max_image_pixels=1_000_000, max_image_decode_pixels=1_100_000),
    )

    with pytest.raises(ImageMetadataError, match="实际 1200×1000") as error:
        await service.process(
            job_id="job_test",
            image_id="img_test",
            object_key="uploads/unsafe.jpg",
        )
    assert error.value.reject_code == "IMAGE_TOO_LARGE"
    assert storage.uploads == {}


def test_perceptual_hash_is_stable_for_the_same_image() -> None:
    image = Image.new("RGB", (128, 128), "white")

    assert build_perceptual_hash(image) == build_perceptual_hash(image.copy())
