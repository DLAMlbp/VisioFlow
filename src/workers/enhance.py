from __future__ import annotations

import asyncio
import logging
from io import BytesIO

from celery import Task
from PIL import Image

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.beautify import NaturalBeautifyService
from src.services.images.metadata import encode_jpeg, make_thumbnail
from src.services.images.quality import QualityEngine
from src.services.images.tagging import PROMPT_VERSION
from src.services.jobs.dispatch import AnalysisTaskPublisher, EmbeddingTaskPublisher
from src.services.profiles import ProfileLoader
from src.services.storage.factory import get_storage_provider
from src.services.storage.keys import build_analysis_object_key, build_enhanced_object_key
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class ImageEnhancementTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        image_id = args[0] if args else kwargs.get("image_id")
        if image_id:
            try:
                asyncio.run(_mark_enhancement_failed(image_id))
            except Exception:
                logger.exception("Unable to mark the failed enhancement")


@celery_app.task(
    bind=True,
    base=ImageEnhancementTask,
    name="image.enhance",
    queue="enhance",
    max_retries=3,
    default_retry_delay=10,
)
def enhance_image(task, image_id: str) -> None:
    try:
        asyncio.run(_enhance_image(image_id))
    except Exception as exc:
        raise task.retry(exc=exc, countdown=10) from exc


async def _enhance_image(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.claim_enhancement(image_id)
        if item is None:
            return
        job = await repository.get_config(item.job_id)
        if job is None or job.cancel_requested_at is not None:
            return
        settings = load_ai_model_settings(get_settings())
        profiles = ProfileLoader(settings)
        profile = profiles.get_beautify_profile(job.beautify_profile_id)
        filter_profile = profiles.get_filter_profile(job.filter_profile_id)
        storage = get_storage_provider()
        beautify_service = NaturalBeautifyService()
        original_bytes = await storage.download(item.object_key)
        orientation_result = beautify_service.normalize_orientation(original_bytes, profile)
        quality_scores = _quality_scores(item)
        assessment = beautify_service.assess_enhancement_need(
            quality_scores=quality_scores,
            rules=filter_profile.hard_rules,
            orientation_result=orientation_result,
        )
        if assessment.needs_enhancement:
            beautify_result = beautify_service.enhance_with_details(
                original_bytes, profile, quality_scores=quality_scores
            )
            enhanced_bytes = beautify_result.image_bytes
            reasons = beautify_service.processing_reasons(profile, beautify_result)
        else:
            enhanced_bytes = beautify_service.prepare_delivery_image(
                orientation_result.image_bytes, profile
            )
            reasons = ["图片质量良好，已跳过不必要的重度美化"]

        enhanced_object_key = build_enhanced_object_key(item.job_id, item.id)
        analysis_object_key = build_analysis_object_key(item.job_id, item.id)
        await storage.upload(enhanced_object_key, enhanced_bytes, "image/jpeg")
        with Image.open(BytesIO(enhanced_bytes)) as enhanced_image:
            enhanced_image.load()
            enhanced_thumbnail = encode_jpeg(
                make_thumbnail(enhanced_image, settings.thumbnail_long_side)
            )
            analysis_bytes = encode_jpeg(
                make_thumbnail(enhanced_image, settings.ai_tagging_image_long_side)
            )
        await storage.upload(analysis_object_key, analysis_bytes, "image/jpeg")
        enhanced_metrics = QualityEngine(settings).evaluate(enhanced_thumbnail)
        enhanced_metric_values = {
            "sharpness": enhanced_metrics.sharpness_score,
            "exposure": enhanced_metrics.exposure_score,
            "contrast": enhanced_metrics.contrast_score,
            "noise": enhanced_metrics.noise_score,
        }
        await repository.complete_enhancement(
            item,
            enhanced_object_key=enhanced_object_key,
            analysis_object_key=analysis_object_key,
            enhanced_metrics=enhanced_metric_values,
            reasons=reasons,
        )
        if await repository.start_tagging(
            item,
            final_score=float(item.result.final_score or 0) if item.result else 0,
            reasons=reasons,
            enhanced_object_key=enhanced_object_key,
            enhanced_metrics=enhanced_metric_values,
            provider=settings.ai_tagging_provider,
            model_name=settings.ai_tagging_model,
            prompt_version=PROMPT_VERSION,
        ):
            AnalysisTaskPublisher().publish(item.id)
            EmbeddingTaskPublisher().publish(item.id)


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


async def _mark_enhancement_failed(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        if item is not None and item.status in {"filtered", "enhancing", "enhanced"}:
            await repository.fail_item(item, "图片美化任务多次重试后仍失败")
