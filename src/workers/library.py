from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.repositories.library import LibraryRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.embedding import ImageEmbeddingError, OpenClipImageEmbedder
from src.services.images.group_match_index import invalidate_group_prototype_index
from src.services.images.group_prototypes import select_diverse_group_prototypes
from src.services.images.metadata import (
    build_perceptual_hash,
    detect_image_content_type,
    encode_jpeg,
    make_thumbnail,
    pillow_format_to_content_type,
)
from src.services.images.tagging import (
    analyze_with_retries,
    get_tag_provider,
    matching_content_payload,
)
from src.services.storage.factory import get_storage_provider
from src.services.storage.keys import build_library_thumbnail_object_key
from src.services.profiles import ProfileLoader
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
    queue="openclip",
    priority=1,
    max_retries=0,
)
def process_library_asset(asset_id: str) -> None:
    asyncio.run(_process_library_asset(asset_id))


async def _process_library_asset(asset_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = LibraryRepository(session)
        asset = await repository.get_asset(asset_id)
        if asset is None or (
            asset.status != "pending"
            and not (asset.status == "active" and asset.analysis_json is None)
        ):
            return
        was_active = asset.status == "active"
        settings = load_ai_model_settings(get_settings())
        try:
            storage = get_storage_provider()
            object_size = await storage.get_size(asset.original_object_key)
            max_size = settings.max_image_size_mb * 1024 * 1024
            if object_size <= 0 or object_size > max_size:
                raise ValueError(f"素材大小不符合限制（最大 {settings.max_image_size_mb}MB）")
            original_bytes = await storage.download(asset.original_object_key)
            prepared = _prepare_library_image(original_bytes)
            reusable = await repository.find_reusable_exact_asset(asset.id, prepared.sha256)
            if reusable is not None:
                content = reusable.analysis_json
                embedding = list(reusable.embedding)
                embedding_version = reusable.embedding_version
            else:
                analysis = await analyze_with_retries(
                    get_tag_provider(settings),
                    prepared.normalized_bytes,
                    settings.ai_tagging_max_retries,
                )
                if analysis.status != "completed" or analysis.payload is None:
                    raise ValueError(analysis.error_message or "素材内容特征识别失败")
                content = matching_content_payload(analysis.payload).model_dump(mode="json")
                embedding = await OpenClipImageEmbedder(settings).embed(
                    prepared.normalized_bytes
                )
                embedding_version = settings.image_embedding_version
            thumbnail_object_key = build_library_thumbnail_object_key(asset.id)
            await storage.upload(
                thumbnail_object_key,
                prepared.normalized_bytes,
                "image/jpeg",
            )
            await repository.update_asset(
                asset,
                {
                    "content_type": prepared.content_type,
                    "file_size": prepared.file_size,
                    "width": prepared.width,
                    "height": prepared.height,
                    "sha256": prepared.sha256,
                    "phash": prepared.phash,
                    "thumbnail_object_key": thumbnail_object_key,
                    "analysis_json": content,
                    "embedding": embedding,
                    "embedding_version": embedding_version,
                    "status": "active",
                    "error_message": None,
                },
            )
            await _rebuild_group_prototypes(repository, asset.group_id)
        except (ImageEmbeddingError, OSError, UnidentifiedImageError, ValueError) as exc:
            emit_metric(
                logger,
                "embedding_failures_total",
                labels={
                    "source": "library",
                    "asset_id": asset.id,
                    "embedding_version": settings.image_embedding_version,
                },
            )
            await repository.update_asset(
                asset,
                (
                    {"error_message": f"内容特征待补全：{str(exc)[:470]}"}
                    if was_active
                    else {"status": "failed", "error_message": str(exc)[:500]}
                ),
            )
        except Exception:
            logger.exception("Unable to process library asset %s", asset_id)
            emit_metric(
                logger,
                "embedding_failures_total",
                labels={
                    "source": "library",
                    "asset_id": asset.id,
                    "embedding_version": settings.image_embedding_version,
                },
            )
            await repository.update_asset(
                asset,
                (
                    {"error_message": "内容特征待补全：素材处理失败"}
                    if was_active
                    else {"status": "failed", "error_message": "素材处理失败"}
                ),
            )


@celery_app.task(name="library.backfill_content_features", queue="openclip", priority=1)
def backfill_library_content_features() -> None:
    asyncio.run(_backfill_library_content_features())


@celery_app.task(
    name="library.rebuild_group_prototypes",
    queue="openclip",
    priority=1,
    max_retries=0,
)
def rebuild_library_group_prototypes(group_id: str) -> None:
    asyncio.run(_rebuild_library_group_prototypes(group_id))


@celery_app.task(
    name="library.backfill_group_prototypes",
    queue="openclip",
    priority=1,
    max_retries=0,
)
def backfill_library_group_prototypes() -> None:
    asyncio.run(_backfill_library_group_prototypes())


async def _backfill_library_content_features() -> None:
    async with AsyncSessionLocal() as session:
        repository = LibraryRepository(session)
        asset_ids = await repository.list_active_assets_missing_analysis(limit=100)
    for asset_id in asset_ids:
        celery_app.send_task(
            "library.process_asset",
            args=[asset_id],
            queue="openclip",
            priority=1,
        )


async def _rebuild_library_group_prototypes(group_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await _rebuild_group_prototypes(LibraryRepository(session), group_id)


async def _backfill_library_group_prototypes() -> None:
    settings = load_ai_model_settings(get_settings())
    profile = ProfileLoader(settings).get_similarity_profile(
        settings.default_similarity_profile
    )
    async with AsyncSessionLocal() as session:
        repository = LibraryRepository(session)
        group_ids = await repository.list_groups_needing_prototype_refresh(
            max_prototypes=profile.similarity_group_max_prototypes,
            limit=100,
        )
        for group_id in group_ids:
            await _rebuild_group_prototypes(repository, group_id, profile=profile)


async def _rebuild_group_prototypes(
    repository: LibraryRepository,
    group_id: str,
    *,
    profile=None,
) -> None:
    settings = load_ai_model_settings(get_settings())
    if profile is None:
        profile = ProfileLoader(settings).get_similarity_profile(
            settings.default_similarity_profile
        )
    assets = await repository.list_active_group_assets_with_embeddings(group_id)
    prototype_ids = select_diverse_group_prototypes(
        assets,
        limit=profile.similarity_group_max_prototypes,
    )
    await repository.replace_group_prototypes(group_id, prototype_ids)
    invalidate_group_prototype_index(settings)


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
