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
from src.services.images.processing_vision import (
    PROCESSING_PROMPT_VERSION,
    beautify_from_processing_json,
    merge_beautify_plan,
    neutralize_beautify_profile,
    selected_standard_from_processing_json,
)
from src.services.images.quality import QualityEngine
from src.services.jobs.dispatch import AnalysisTaskPublisher, EmbeddingTaskPublisher
from src.services.managed_profiles import (
    beautify_from_snapshot,
    legacy_standard_from_snapshots,
    passthrough_standard,
    standards_from_snapshots,
)
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
        standards = standards_from_snapshots(job.processing_standard_snapshots)
        if not job.filter_enabled:
            standards = [passthrough_standard()]
        elif not standards:
            standards = [
                legacy_standard_from_snapshots(
                    job.filter_profile_snapshot,
                    job.beautify_profile_snapshot,
                    job.filter_profile_id,
                    job.beautify_profile_id,
                )
            ]
        selected_standard_id, _ = selected_standard_from_processing_json(item.ai_processing_json)
        if selected_standard_id and not any(
            candidate.id == selected_standard_id for candidate in standards
        ):
            await repository.fail_item(item, "命中的条件过滤标准不存在，请重试图片处理")
            return
        profile = beautify_from_snapshot(
            job.beautify_profile_snapshot, job.beautify_profile_id, settings
        )
        storage = get_storage_provider()
        beautify_service = NaturalBeautifyService()
        original_bytes = await storage.download(item.object_key)
        neutral_profile = neutralize_beautify_profile(profile)
        orientation_result = beautify_service.normalize_orientation(
            original_bytes, neutral_profile
        )
        ai_beautify = (
            beautify_from_processing_json(item.ai_processing_json)
            if item.ai_processing_status == "completed"
            else None
        )
        if job.beautify_enabled and ai_beautify is None:
            await repository.fail_item(
                item, "缺少 AI 美化决策，请重试图片处理"
            )
            return
        effective_profile = (
            merge_beautify_plan(profile, ai_beautify)
            if job.beautify_enabled and ai_beautify is not None
            else neutral_profile
        )
        if job.beautify_enabled and ai_beautify is not None and ai_beautify.needed:
            beautify_result = beautify_service.enhance_with_details(
                original_bytes, effective_profile
            )
            enhanced_bytes = beautify_result.image_bytes
            reasons = beautify_service.processing_reasons(
                effective_profile, beautify_result
            )
            reasons.insert(0, f"AI 美化：{ai_beautify.reason}")
        else:
            enhanced_bytes = beautify_service.prepare_delivery_image(
                orientation_result.image_bytes, neutral_profile
            )
            reasons = (
                [f"AI 美化：{ai_beautify.reason}，无需额外调整"]
                if ai_beautify is not None and job.beautify_enabled
                else ["已跳过美化，仅校正方向并规范输出格式"]
            )

        enhanced_object_key = build_enhanced_object_key(item.job_id, item.id)
        analysis_object_key = (
            build_analysis_object_key(item.job_id, item.id)
            if job.similarity_enabled
            else enhanced_object_key
        )
        await storage.upload(enhanced_object_key, enhanced_bytes, "image/jpeg")
        with Image.open(BytesIO(enhanced_bytes)) as enhanced_image:
            enhanced_image.load()
            enhanced_thumbnail = encode_jpeg(
                make_thumbnail(enhanced_image, settings.thumbnail_long_side)
            )
            if job.similarity_enabled:
                analysis_bytes = encode_jpeg(
                    make_thumbnail(enhanced_image, settings.ai_tagging_image_long_side)
                )
        if job.similarity_enabled:
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
        if not job.similarity_enabled:
            await repository.select_item(
                item,
                enhanced_object_key,
                float(item.result.final_score or 0) if item.result else 0,
                reasons=[*reasons, "已跳过素材相似匹配"],
                enhanced_metrics=enhanced_metric_values,
            )
            return
        if await repository.start_tagging(
            item,
            final_score=float(item.result.final_score or 0) if item.result else 0,
            reasons=reasons,
            enhanced_object_key=enhanced_object_key,
            enhanced_metrics=enhanced_metric_values,
            provider=settings.ai_tagging_provider,
            model_name=settings.ai_tagging_model,
            prompt_version=PROCESSING_PROMPT_VERSION,
        ):
            AnalysisTaskPublisher().publish(item.id)
            EmbeddingTaskPublisher().publish(item.id)


async def _mark_enhancement_failed(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        if item is not None and item.status in {"filtered", "enhancing", "enhanced"}:
            await repository.fail_item(item, "图片美化任务多次重试后仍失败")
