from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.library import LibraryRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.embedding import ImageEmbeddingError, OpenClipImageEmbedder
from src.services.images.metadata import (
    build_perceptual_hash,
    detect_image_content_type,
    encode_jpeg,
    make_thumbnail,
    pillow_format_to_content_type,
)
from src.services.images.tagging import get_tag_provider
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedLibraryImage:
    content_type: str
    file_size: int
    width: int
    height: int
    sha256: str
    phash: str
    normalized_bytes: bytes


@celery_app.task(
    name="library.process_asset",
    queue="library",
    max_retries=0,
)
def process_library_asset(asset_id: str) -> None:
    asyncio.run(_process_library_asset(asset_id))


async def _process_library_asset(asset_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = LibraryRepository(session)
        asset = await repository.get_asset(asset_id)
        if asset is None or asset.status != "pending":
            return
        settings = load_ai_model_settings(get_settings())
        try:
            storage = get_storage_provider()
            object_size = await storage.get_size(asset.original_object_key)
            max_size = settings.max_image_size_mb * 1024 * 1024
            if object_size <= 0 or object_size > max_size:
                raise ValueError(f"素材大小不符合限制（最大 {settings.max_image_size_mb}MB）")
            original_bytes = await storage.download(asset.original_object_key)
            prepared = _prepare_library_image(original_bytes)
            duplicate = await repository.find_duplicate_asset(
                asset.id, prepared.sha256, prepared.phash
            )
            if duplicate is not None:
                raise ValueError(f"素材库已存在相同图片：{duplicate.id}")

            outcome = await get_tag_provider(settings).tag(prepared.normalized_bytes)
            if outcome.status != "completed" or outcome.payload is None:
                raise ValueError(outcome.error_message or "素材内容分析失败")
            embedding = await OpenClipImageEmbedder(settings).embed(prepared.normalized_bytes)
            await repository.update_asset(
                asset,
                {
                    "content_type": prepared.content_type,
                    "file_size": prepared.file_size,
                    "width": prepared.width,
                    "height": prepared.height,
                    "sha256": prepared.sha256,
                    "phash": prepared.phash,
                    "analysis_json": outcome.payload.model_dump(),
                    "embedding": embedding,
                    "embedding_version": settings.image_embedding_version,
                    "status": "active",
                    "error_message": None,
                },
            )
        except (ImageEmbeddingError, OSError, UnidentifiedImageError, ValueError) as exc:
            await repository.update_asset(
                asset,
                {"status": "failed", "error_message": str(exc)[:500]},
            )
        except Exception:
            logger.exception("Unable to process library asset %s", asset_id)
            await repository.update_asset(
                asset,
                {"status": "failed", "error_message": "素材处理失败"},
            )


def _prepare_library_image(image_bytes: bytes) -> PreparedLibraryImage:
    content_type = detect_image_content_type(image_bytes)
    if content_type is None:
        raise ValueError("不支持的图片格式或文件已损坏")
    with Image.open(BytesIO(image_bytes)) as verification:
        verification.verify()
    with Image.open(BytesIO(image_bytes)) as source:
        if pillow_format_to_content_type(source.format) != content_type:
            raise ValueError("图片格式与文件内容不一致")
        normalized = ImageOps.exif_transpose(source).convert("RGB")
        width, height = normalized.size
        analysis_image = make_thumbnail(normalized, 1024)
        normalized_bytes = encode_jpeg(analysis_image)
        phash = build_perceptual_hash(analysis_image)
    return PreparedLibraryImage(
        content_type=content_type,
        file_size=len(image_bytes),
        width=width,
        height=height,
        sha256=sha256(image_bytes).hexdigest(),
        phash=phash,
        normalized_bytes=normalized_bytes,
    )
