from __future__ import annotations

import asyncio
import logging

from celery import Task

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.beautify_planning import (
    BEAUTIFY_PLAN_PROMPT_VERSION,
    BeautifyPlanningService,
    build_stored_plan,
)
from src.services.images.quality import QualityEngine
from src.services.images.vision_rate_limit import acquire_vision_rate_slot
from src.services.jobs.dispatch import EnhancementTaskPublisher
from src.services.managed_profiles import beautify_from_snapshot
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class BeautifyPlanningTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        image_id = args[0] if args else kwargs.get("image_id")
        if image_id:
            try:
                asyncio.run(_mark_beautify_plan_failed(image_id))
            except Exception:
                logger.exception("Unable to mark beautify planning as failed")


@celery_app.task(
    bind=True,
    base=BeautifyPlanningTask,
    name="image.plan_beautify",
    queue="beautify_plan",
    max_retries=3,
    default_retry_delay=10,
)
def plan_beautify(task, image_id: str) -> None:
    try:
        asyncio.run(_plan_beautify(image_id))
    except Exception as exc:
        raise task.retry(exc=exc, countdown=10) from exc


async def _plan_beautify(image_id: str) -> None:
    if not get_settings().post_filter_beautify_plan_enabled:
        logger.error(
            "Post-filter beautify planning is disabled; image remains pending image_id=%s", image_id
        )
        return
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.claim_beautify_plan(image_id)
        if item is None:
            return
        job = await repository.get_config(item.job_id)
        if job is None or job.cancel_requested_at is not None:
            return

        settings = load_ai_model_settings(get_settings())
        profile = beautify_from_snapshot(
            job.beautify_profile_snapshot, job.beautify_profile_id, settings
        )
        image_bytes = await get_storage_provider().download(item.object_key)
        if settings.ai_tagging_enabled and settings.ai_tagging_api_key:
            await acquire_vision_rate_slot(settings)
        emit_metric(
            logger,
            "beautify_plan_requests_total",
            labels={
                "job_id": item.job_id,
                "image_id": item.id,
                "beautify_profile_id": job.beautify_profile_id,
                "beautify_profile_version": (job.beautify_profile_snapshot or {}).get("version"),
            },
        )
        outcome = await BeautifyPlanningService(settings).analyze(
            image_bytes,
            instruction=_profile_instruction(job.beautify_profile_snapshot, profile.description),
            image_context=_image_context(item),
        )
        if outcome.status != "completed" or outcome.payload is None:
            emit_metric(
                logger,
                "beautify_plan_failures_total",
                labels={
                    "job_id": item.job_id,
                    "image_id": item.id,
                    "beautify_profile_id": job.beautify_profile_id,
                },
            )
            if outcome.retryable:
                await repository.reset_beautify_plan_for_retry(item.id)
                raise RuntimeError(outcome.error_message or "美化规划调用失败")
            await repository.save_beautify_plan(
                item.id,
                status="failed",
                model_name=settings.ai_tagging_model,
                prompt_version=BEAUTIFY_PLAN_PROMPT_VERSION,
                duration_ms=outcome.duration_ms,
                payload=None,
                error_message=outcome.error_message,
            )
            await repository.fail_item(
                item, f"美化规划失败：{outcome.error_message or '模型响应无效'}"
            )
            return

        try:
            stored_plan = build_stored_plan(profile, outcome.payload)
        except ValueError as exc:
            await repository.save_beautify_plan(
                item.id,
                status="failed",
                model_name=settings.ai_tagging_model,
                prompt_version=BEAUTIFY_PLAN_PROMPT_VERSION,
                duration_ms=outcome.duration_ms,
                payload=None,
                error_message=str(exc),
            )
            await repository.fail_item(item, f"美化规划不符合策略：{exc}")
            return

        saved = await repository.save_beautify_plan(
            item.id,
            status="completed",
            model_name=settings.ai_tagging_model,
            prompt_version=BEAUTIFY_PLAN_PROMPT_VERSION,
            duration_ms=outcome.duration_ms,
            payload=stored_plan.model_dump(mode="json"),
            error_message=None,
        )
        if saved:
            EnhancementTaskPublisher().publish(item.id)


async def _mark_beautify_plan_failed(image_id: str) -> None:
    emit_metric(logger, "beautify_plan_failures_total", labels={"image_id": image_id})
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        if item is not None and item.status in {"filtered", "beautify_planning"}:
            await repository.fail_item(item, "美化规划任务多次重试后仍失败")


def _image_context(item) -> dict[str, int | float]:
    values: dict[str, int | float] = {
        "width": int(item.width or 0),
        "height": int(item.height or 0),
    }
    metric = item.metric
    if metric is not None:
        values.update(
            {
                "sharpness_score": float(metric.sharpness_score),
                "exposure_score": float(metric.exposure_score),
                "contrast_score": float(metric.contrast_score),
                "noise_score": float(metric.noise_score),
                "quality_score": QualityEngine.calculate_weighted_quality_score(
                    sharpness=float(metric.sharpness_score),
                    exposure=float(metric.exposure_score),
                    contrast=float(metric.contrast_score),
                    noise=float(metric.noise_score),
                ),
            }
        )
    return values


def _profile_instruction(snapshot: dict[str, object] | None, fallback: str) -> str:
    if snapshot and isinstance(snapshot.get("instruction"), str):
        instruction = str(snapshot["instruction"]).strip()
        if instruction:
            return instruction
    return fallback
