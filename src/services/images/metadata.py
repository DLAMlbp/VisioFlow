from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from src.core.config import Settings
from src.services.storage.interfaces import StorageProvider
from src.services.storage.keys import build_thumbnail_object_key

MAGIC_BYTES_CONTENT_TYPES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}


class ImageMetadataError(Exception):
    pass


@dataclass(frozen=True)
class ImageMetadata:
    content_type: str
    file_size: int
    width: int
    height: int
    aspect_ratio: float
    orientation: int | None
    sha256: str
    phash: str
    thumbnail_object_key: str
    thumbnail_bytes: bytes
    original_bytes: bytes


class ImageMetadataService:
    def __init__(self, storage: StorageProvider, settings: Settings) -> None:
        self.storage = storage
        self.settings = settings

    async def process(self, *, job_id: str, image_id: str, object_key: str) -> ImageMetadata:
        object_size = await self.storage.get_size(object_key)
        max_size = self.settings.max_image_size_mb * 1024 * 1024
        if object_size <= 0 or object_size > max_size:
            raise ImageMetadataError(f"图片实际大小不符合限制（最大 {self.settings.max_image_size_mb}MB）")

        image_bytes = await self.storage.download(object_key)
        if len(image_bytes) != object_size:
            raise ImageMetadataError("图片读取大小与对象存储元数据不一致")
        content_type = detect_image_content_type(image_bytes)
        if content_type is None or content_type not in self.settings.allowed_image_content_types:
            raise ImageMetadataError("不支持的图片格式或 Magic Bytes 无效")

        try:
            with Image.open(BytesIO(image_bytes)) as image:
                image.verify()
            with Image.open(BytesIO(image_bytes)) as image:
                actual_content_type = pillow_format_to_content_type(image.format)
                if actual_content_type != content_type:
                    raise ImageMetadataError("图片格式与 Magic Bytes 不一致")

                orientation = image.getexif().get(274)
                width, height = image.size
                thumbnail = make_thumbnail(image, self.settings.thumbnail_long_side)
        except (Image.DecompressionBombError, OSError, UnidentifiedImageError) as exc:
            raise ImageMetadataError("图片无法解码或已损坏") from exc

        thumbnail_object_key = build_thumbnail_object_key(job_id, image_id)
        thumbnail_bytes = encode_jpeg(thumbnail)
        await self.storage.upload(thumbnail_object_key, thumbnail_bytes, "image/jpeg")

        return ImageMetadata(
            content_type=content_type,
            file_size=len(image_bytes),
            width=width,
            height=height,
            aspect_ratio=round(width / height, 4),
            orientation=orientation,
            sha256=sha256(image_bytes).hexdigest(),
            phash=build_perceptual_hash(thumbnail),
            thumbnail_object_key=thumbnail_object_key,
            thumbnail_bytes=thumbnail_bytes,
            original_bytes=image_bytes,
        )


def detect_image_content_type(image_bytes: bytes) -> str | None:
    for magic, content_type in MAGIC_BYTES_CONTENT_TYPES.items():
        if image_bytes.startswith(magic):
            return content_type
    if (
        len(image_bytes) >= 12
        and image_bytes.startswith(b"RIFF")
        and image_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def pillow_format_to_content_type(image_format: str | None) -> str | None:
    return {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(image_format)


def make_thumbnail(image: Image.Image, long_side: int) -> Image.Image:
    thumbnail = ImageOps.exif_transpose(image).copy()
    thumbnail.thumbnail((long_side, long_side), Image.Resampling.LANCZOS)
    if thumbnail.mode not in {"RGB", "L"}:
        background = Image.new("RGB", thumbnail.size, "white")
        if thumbnail.mode == "RGBA":
            background.paste(thumbnail, mask=thumbnail.getchannel("A"))
        else:
            background.paste(thumbnail.convert("RGB"))
        thumbnail = background
    return thumbnail


def encode_jpeg(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="JPEG", quality=85, optimize=True)
    return output.getvalue()


def build_perceptual_hash(image: Image.Image) -> str:
    grayscale = image.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
    pixels = list(grayscale.get_flattened_data())
    average = sum(pixels) / len(pixels)
    return "".join("1" if pixel >= average else "0" for pixel in pixels)
