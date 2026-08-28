import asyncio
import logging
from io import BytesIO

from celery import Task
from PIL import Image

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.schemas.jobs import ImageItemStatus
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.beautify import NaturalBeautifyService
from src.services.images.hard_filter import HardFilterService, RejectCode
from src.services.images.metadata import (
    ImageMetadataError,
    ImageMetadataService,
    encode_jpeg,
    make_thumbnail,
)
from src.services.images.processing_vision import (
    PROCESSING_PROMPT_VERSION,
    ProcessingVisionService,
    neutralize_beautify_profile,
)
from src.services.images.quality import QualityEngine
from src.services.images.vision_rate_limit import acquire_vision_rate_slot
from src.services.jobs.dispatch import EnhancementTaskPublisher, RankingTaskPublisher
from src.services.managed_profiles import beautify_from_snapshot, filter_from_snapshot
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
            await _advance_after_preprocess(repository, item)
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

        item = await repository.claim_preprocess(image_id)
        if item is None:
            return
        settings = load_ai_model_settings(get_settings())
        job = await repository.get_config(item.job_id)
        if job is None:
            return
        filter_profile = filter_from_snapshot(
            job.filter_profile_snapshot, job.filter_profile_id, settings
        )
        beautify_profile = beautify_from_snapshot(
            job.beautify_profile_snapshot, job.beautify_profile_id, settings
        )
        storage = get_storage_provider()

        service = ImageMetadataService(storage, settings)
        try:
            metadata = await service.process(
                job_id=item.job_id,
                image_id=item.id,
                object_key=item.object_key,
            )
        except ImageMetadataError as exc:
            await repository.fail_item(item, str(exc))
            await _advance_after_preprocess(repository, item)
            return

        beautify_service = NaturalBeautifyService()
        neutral_beautify_profile = neutralize_beautify_profile(beautify_profile)
        orientation_result = beautify_service.normalize_orientation(
            metadata.original_bytes,
            neutral_beautify_profile,
        )
        with Image.open(BytesIO(orientation_result.image_bytes)) as normalized_image:
            normalized_image.load()
            normalized_width, normalized_height = normalized_image.size
            normalized_thumbnail = encode_jpeg(
                make_thumbnail(normalized_image, settings.thumbnail_long_side)
            )

        duplicate, similar = await repository.save_metadata_and_find_duplicates(
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
            max_hamming_distance=settings.technical_duplicate_hamming_distance,
        )
        if duplicate is not None:
            await repository.reject_item(item, [RejectCode.DUPLICATE_IMAGE])
            await _advance_after_preprocess(repository, item)
            return

        warnings: list[str] = []
        if similar is not None:
            warnings.append("与同批次照片构图相似，已保留，请按需确认是否重复")

        hard_filter = HardFilterService(settings)
        filter_result = hard_filter.evaluate(
            normalized_thumbnail,
            width=normalized_width,
            height=normalized_height,
        )
        if not filter_result.passed:
            await repository.reject_item(item, list(filter_result.reject_codes))
            await _advance_after_preprocess(repository, item)
            return

        quality_metrics = QualityEngine(settings).evaluate(normalized_thumbnail)
        await repository.upsert_metrics(item.id, quality_metrics.as_db_values())
        final_score = QualityEngine.calculate_weighted_quality_score(
            sharpness=quality_metrics.sharpness_score,
            exposure=quality_metrics.exposure_score,
            contrast=quality_metrics.contrast_score,
            noise=quality_metrics.noise_score,
        )
        if settings.ai_tagging_enabled and settings.ai_tagging_api_key:
            await acquire_vision_rate_slot(settings)
        ai_outcome = await ProcessingVisionService(settings).analyze(
            orientation_result.image_bytes,
            filter_instruction=_profile_instruction(
                job.filter_profile_snapshot, filter_profile.description
            ),
            beautify_instruction=_profile_instruction(
                job.beautify_profile_snapshot, beautify_profile.description
            ),
            image_context={
                "width": normalized_width,
                "height": normalized_height,
                "sharpness_score": quality_metrics.sharpness_score,
                "exposure_score": quality_metrics.exposure_score,
                "contrast_score": quality_metrics.contrast_score,
                "noise_score": quality_metrics.noise_score,
                "quality_score": final_score,
            },
        )
        await repository.save_ai_processing(
            item,
            status=ai_outcome.status,
            model_name=settings.ai_tagging_model,
            prompt_version=PROCESSING_PROMPT_VERSION,
            duration_ms=ai_outcome.duration_ms,
            payload=(
                ai_outcome.payload.model_dump(mode="json")
                if ai_outcome.payload is not None
                else None
            ),
            error_message=ai_outcome.error_message,
        )
        if ai_outcome.status != "completed" or ai_outcome.payload is None:
            await repository.fail_item(
                item,
                f"AI 图片处理失败：{ai_outcome.error_message or '模型未返回有效结果'}",
            )
            await _advance_after_preprocess(repository, item)
            return
        if (
            ai_outcome.status == "completed"
            and ai_outcome.payload.filter.decision == "reject"
        ):
            await repository.reject_item(
                item,
                [RejectCode.AI_FILTER_REJECTED],
                reason=ai_outcome.payload.filter.reason,
            )
            await _advance_after_preprocess(repository, item)
            return
        warnings.append(f"AI 筛选：{ai_outcome.payload.filter.reason}")

        await repository.complete_filter(
            item,
            final_score=final_score,
            reasons=warnings,
        )
        await _advance_after_preprocess(repository, item)


async def _advance_after_preprocess(repository: ImageJobRepository, item) -> None:
    job = await repository.get_config(item.job_id)
    if job is None or job.cancel_requested_at is not None:
        return
    if job.max_selected >= job.total_count:
        if item.status == ImageItemStatus.FILTERED.value:
            EnhancementTaskPublisher().publish(item.id)
        return
    if await repository.claim_ranking_if_filtering_complete(job.id):
        RankingTaskPublisher().publish(job.id)


async def _mark_item_failed(image_id: str, reason: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        if item is not None:
            await repository.fail_item(item, reason)


def _profile_instruction(snapshot: dict[str, object] | None, fallback: str) -> str:
    if snapshot and isinstance(snapshot.get("instruction"), str):
        instruction = str(snapshot["instruction"]).strip()
        if instruction:
            return instruction
    return fallback
