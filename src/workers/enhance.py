from __future__ import annotations

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
from src.services.images.metadata import encode_jpeg, make_thumbnail
from src.services.images.quality import QualityEngine
from src.services.jobs.dispatch import TaggingTaskPublisher
from src.services.profiles import ProfileLoader
from src.services.storage.factory import get_storage_provider
from src.services.storage.keys import build_enhanced_object_key
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class EnhancementBatchTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        job_id = args[0] if args else kwargs.get("job_id")
        if job_id:
            try:
                asyncio.run(_fail_pending_enhancements(job_id))
            except Exception:
                logger.exception("Unable to mark the failed enhancement batch")


@celery_app.task(
    bind=True,
    base=EnhancementBatchTask,
    name="image.enhance_batch",
    queue="enhance",
    max_retries=3,
    default_retry_delay=10,
)
def enhance_image_batch(task, job_id: str) -> None:
    try:
        asyncio.run(_enhance_image_batch(job_id))
    except Exception as exc:
        raise task.retry(exc=exc, countdown=10) from exc


async def _enhance_image_batch(job_id: str) -> None:
    tag_image_ids: list[str] = []
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        job = await repository.get(job_id)
        if job is None or job.status not in {"enhancing", "tagging"}:
            return

        settings = load_ai_model_settings(get_settings())
        profile = ProfileLoader(settings).get_beautify_profile(job.beautify_profile_id)
        storage = get_storage_provider()
        beautify_service = NaturalBeautifyService()

        # The batch task does not create a tag until every retained item is enhanced.
        for item in job.items:
            if item.status not in {
                ImageItemStatus.FILTERED.value,
                ImageItemStatus.ENHANCING.value,
            }:
                continue
            await _enhance_item(
                repository=repository,
                item=item,
                storage=storage,
                beautify_service=beautify_service,
                profile=profile,
                thumbnail_long_side=settings.thumbnail_long_side,
            )

        refreshed_job = await repository.get(job_id)
        if refreshed_job is None:
            return
        for item in refreshed_job.items:
            if item.status != ImageItemStatus.ENHANCED.value or item.result is None:
                continue
            enhanced_metrics = item.result.enhanced_metrics_json
            enhanced_object_key = item.result.enhanced_object_key
            if enhanced_metrics is None or enhanced_object_key is None:
                raise RuntimeError(f"美化结果不完整：{item.id}")
            if await repository.start_tagging(
                item,
                final_score=float(item.result.final_score or 0),
                reasons=item.result.reasons_json or [],
                enhanced_object_key=enhanced_object_key,
                enhanced_metrics=enhanced_metrics,
                provider=settings.ai_tagging_provider,
                model_name=settings.ai_tagging_model,
                prompt_version="renovation_auto_generic_v1",
            ):
                tag_image_ids.append(item.id)

        await repository.complete_job_if_finished(job_id)

    # Dispatch only after the database contains a fully enhanced batch.
    for image_id in tag_image_ids:
        TaggingTaskPublisher().publish(image_id)


async def _enhance_item(
    *,
    repository: ImageJobRepository,
    item,
    storage,
    beautify_service: NaturalBeautifyService,
    profile,
    thumbnail_long_side: int,
) -> None:
    if item.status == ImageItemStatus.FILTERED.value and not await repository.start_enhancing(item):
        return

    original_bytes = await storage.download(item.object_key)
    orientation_result = beautify_service.normalize_orientation(original_bytes, profile)
    quality_scores = _quality_scores(item)
    beautify_result = beautify_service.enhance_with_details(
        orientation_result.image_bytes,
        profile,
        quality_scores=quality_scores,
    )
    enhanced_object_key = build_enhanced_object_key(item.job_id, item.id)
    await storage.upload(enhanced_object_key, beautify_result.image_bytes, "image/jpeg")
    with Image.open(BytesIO(beautify_result.image_bytes)) as enhanced_image:
        enhanced_image.load()
        enhanced_thumbnail = encode_jpeg(make_thumbnail(enhanced_image, thumbnail_long_side))
    enhanced_metrics = QualityEngine(get_settings()).evaluate(enhanced_thumbnail)
    reasons = beautify_service.processing_reasons(profile, beautify_result)
    await repository.complete_enhancement(
        item,
        enhanced_object_key=enhanced_object_key,
        enhanced_metrics={
            "sharpness": enhanced_metrics.sharpness_score,
            "exposure": enhanced_metrics.exposure_score,
            "contrast": enhanced_metrics.contrast_score,
            "noise": enhanced_metrics.noise_score,
        },
        reasons=reasons,
    )


def _quality_scores(item) -> dict[str, float]:
    if item.metric is None:
        raise RuntimeError(f"缺少筛选质量指标：{item.id}")
    raw_metrics = item.metric.raw_metrics_json or {}
    return {
        "sharpness": item.metric.sharpness_score,
        "exposure": item.metric.exposure_score,
        "contrast": item.metric.contrast_score,
        "noise": item.metric.noise_score,
        "brightness_mean": float(raw_metrics.get("brightness_mean", 128)),
    }


async def _fail_pending_enhancements(job_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        job = await repository.get(job_id)
        if job is None:
            return
        for item in job.items:
            if item.status in {
                ImageItemStatus.FILTERED.value,
                ImageItemStatus.ENHANCING.value,
                ImageItemStatus.ENHANCED.value,
            }:
                await repository.fail_item(item, "批量美化任务多次重试后仍失败")
        await repository.complete_job_if_finished(job_id)
