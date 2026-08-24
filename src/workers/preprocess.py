import asyncio
import logging
from io import BytesIO

from celery import Task
from PIL import Image

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.schemas.jobs import ImageItemStatus
from src.services.images.beautify import NaturalBeautifyService
from src.services.images.hard_filter import HardFilterService, RejectCode
from src.services.images.metadata import (
    ImageMetadataError,
    ImageMetadataService,
    encode_jpeg,
    make_thumbnail,
)
from src.services.images.quality import QualityEngine
from src.services.jobs.dispatch import EnhancementBatchTaskPublisher
from src.services.profiles import ProfileLoader
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class ImagePreprocessTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        image_id = args[0] if args else kwargs.get("image_id")
        if image_id:
            try:
                asyncio.run(_mark_item_failed(image_id, "图片处理任务多次重试后仍失败"))
            except Exception:
                logger.exception("Unable to mark image as failed after task retry exhaustion")


@celery_app.task(
    bind=True,
    base=ImagePreprocessTask,
    name="image.preprocess_metadata",
    queue="preprocess",
    max_retries=3,
    default_retry_delay=10,
)
def preprocess_image_metadata(task, image_id: str) -> None:
    try:
        asyncio.run(_preprocess_image_metadata(image_id))
    except Exception as exc:
        raise task.retry(exc=exc, countdown=10) from exc


async def _preprocess_image_metadata(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        if item is None:
            return
        if item.status == ImageItemStatus.FILTERED.value:
            await _start_enhancement_batch_if_ready(repository, item.job_id)
            return
        if item.status in {
            ImageItemStatus.ENHANCING.value,
            ImageItemStatus.ENHANCED.value,
            ImageItemStatus.TAGGING.value,
            ImageItemStatus.REJECTED.value,
            ImageItemStatus.SELECTED.value,
            ImageItemStatus.FAILED.value,
        }:
            return

        settings = get_settings()
        job = await repository.get(item.job_id)
        if job is None:
            return
        profiles = ProfileLoader(settings)
        filter_profile = profiles.get_filter_profile(job.filter_profile_id)
        beautify_profile = profiles.get_beautify_profile(job.beautify_profile_id)
        storage = get_storage_provider()

        await repository.start_item(item)
        service = ImageMetadataService(storage, settings)
        try:
            metadata = await service.process(
                job_id=item.job_id,
                image_id=item.id,
                object_key=item.object_key,
            )
        except ImageMetadataError as exc:
            await repository.fail_item(item, str(exc))
            await _start_enhancement_batch_if_ready(repository, item.job_id)
            return

        beautify_service = NaturalBeautifyService()
        orientation_result = beautify_service.normalize_orientation(
            metadata.original_bytes,
            beautify_profile,
        )
        with Image.open(BytesIO(orientation_result.image_bytes)) as normalized_image:
            normalized_image.load()
            normalized_width, normalized_height = normalized_image.size
            normalized_thumbnail = encode_jpeg(
                make_thumbnail(normalized_image, settings.thumbnail_long_side)
            )

        await repository.update_item(
            item,
            {
                "content_type": metadata.content_type,
                "file_size": metadata.file_size,
                "width": normalized_width,
                "height": normalized_height,
                "aspect_ratio": round(normalized_width / normalized_height, 4),
                "exif_orientation": metadata.orientation,
                "sha256": metadata.sha256,
                "phash": metadata.phash,
                "thumbnail_object_key": metadata.thumbnail_object_key,
            },
        )

        duplicate = await repository.find_duplicate_item(
            job_id=item.job_id,
            image_id=item.id,
            sha256=metadata.sha256,
        )
        if duplicate is not None:
            await repository.reject_item(item, [RejectCode.DUPLICATE_IMAGE])
            await _start_enhancement_batch_if_ready(repository, item.job_id)
            return

        warnings: list[str] = []
        similar = await repository.find_similar_item(
            job_id=item.job_id,
            image_id=item.id,
            phash=metadata.phash,
            max_hamming_distance=filter_profile.hard_rules.duplicate_hamming_distance,
        )
        if similar is not None:
            warnings.append("与同批次照片构图相似，已保留，请按需确认是否重复")

        hard_filter = HardFilterService(settings, filter_profile.hard_rules)
        filter_result = hard_filter.evaluate(
            normalized_thumbnail,
            width=normalized_width,
            height=normalized_height,
        )
        if not filter_result.passed:
            await repository.reject_item(item, list(filter_result.reject_codes))
            await _start_enhancement_batch_if_ready(repository, item.job_id)
            return

        quality_metrics = QualityEngine(settings).evaluate(normalized_thumbnail)
        await repository.upsert_metrics(item.id, quality_metrics.as_db_values())
        quality_warning_codes = list(filter_result.warning_codes)
        if quality_metrics.sharpness_score < filter_profile.hard_rules.min_sharpness_score:
            quality_warning_codes.append(RejectCode.SHARPNESS_SCORE_TOO_LOW)
        if quality_metrics.exposure_score < filter_profile.hard_rules.min_exposure_score:
            quality_warning_codes.append(RejectCode.EXPOSURE_SCORE_TOO_LOW)
        if quality_metrics.contrast_score < filter_profile.hard_rules.min_contrast_score:
            quality_warning_codes.append(RejectCode.CONTRAST_SCORE_TOO_LOW)
        if quality_metrics.noise_score < filter_profile.hard_rules.min_noise_score:
            quality_warning_codes.append(RejectCode.NOISE_SCORE_TOO_LOW)

        final_score = QualityEngine.calculate_weighted_quality_score(
            sharpness=quality_metrics.sharpness_score,
            exposure=quality_metrics.exposure_score,
            contrast=quality_metrics.contrast_score,
            noise=quality_metrics.noise_score,
        )
        if final_score < filter_profile.hard_rules.min_quality_score:
            await repository.reject_item(item, [RejectCode.QUALITY_SCORE_TOO_LOW])
            await _start_enhancement_batch_if_ready(repository, item.job_id)
            return
        warnings.extend(_quality_warning_text(code) for code in quality_warning_codes)

        await repository.complete_filter(
            item,
            final_score=final_score,
            reasons=warnings,
        )
        await _start_enhancement_batch_if_ready(repository, item.job_id)


async def _start_enhancement_batch_if_ready(repository: ImageJobRepository, job_id: str) -> None:
    if await repository.claim_enhancement_phase_if_filtering_complete(job_id):
        EnhancementBatchTaskPublisher().publish(job_id)


async def _mark_item_failed(image_id: str, reason: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        if item is not None:
            await repository.fail_item(item, reason)


def _quality_warning_text(code: str) -> str:
    labels = {
        RejectCode.EXTREME_BLUR: "检测到画面可能严重模糊，已保留，请查看原图确认",
        RejectCode.EXTREME_OVEREXPOSURE: "检测到大面积高光，已保留并尝试恢复高光细节",
        RejectCode.EXTREME_UNDEREXPOSURE: "检测到大面积暗部，已保留并尝试提亮阴影细节",
        RejectCode.SOLID_COLOR: "检测到画面内容较单一，已保留，请确认是否为有效取证照片",
        RejectCode.SHARPNESS_SCORE_TOO_LOW: "清晰度偏低，已保留，请按需确认",
        RejectCode.EXPOSURE_SCORE_TOO_LOW: "曝光偏离理想范围，已保留并尝试自然校正",
        RejectCode.CONTRAST_SCORE_TOO_LOW: "对比度偏低，已保留并尝试自然增强",
        RejectCode.NOISE_SCORE_TOO_LOW: "噪点偏高，已保留并尝试降噪",
        RejectCode.QUALITY_SCORE_TOO_LOW: "综合质量偏低，已保留，请按需确认",
    }
    return labels[code]
