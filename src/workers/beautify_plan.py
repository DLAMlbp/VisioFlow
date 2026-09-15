from __future__ import annotations

import asyncio
import logging

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.beautify_planning import (
    BEAUTIFY_PLAN_PROMPT_VERSION,
    BeautifyPlanInput,
    BeautifyPlanningService,
    BeautifyPlanOutcome,
    build_stored_plan,
)
from src.services.images.processing_vision import (
    PROCESSING_PROMPT_VERSION,
    beautify_plan_from_processing_json,
)
from src.services.images.quality import QualityEngine
from src.services.jobs.dispatch import RedactionDetectionTaskPublisher
from src.services.managed_profiles import beautify_from_snapshot
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="image.plan_beautify",
    queue="beautify_plan",
    max_retries=0,
)
def plan_beautify(image_id: str) -> None:
    asyncio.run(_plan_beautify(image_id))


async def _plan_beautify(image_id: str) -> None:
    workflow_settings = get_settings()
    if not workflow_settings.post_filter_beautify_plan_enabled:
        logger.error(
            "Post-filter beautify planning is disabled; image remains pending image_id=%s", image_id
        )
        return
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        items = await repository.claim_beautify_plan_batch(
            image_id, limit=workflow_settings.ai_beautify_batch_size
        )
        if not items:
            return
        job = await repository.get_config(items[0].job_id)
        if job is None or job.cancel_requested_at is not None:
            return

        settings = load_ai_model_settings(workflow_settings)
        profile = beautify_from_snapshot(
            job.beautify_profile_snapshot, job.beautify_profile_id, settings
        )
        instruction = _profile_instruction(
            job.beautify_profile_snapshot, profile.description
        )
        storage = get_storage_provider()
        pending_items = []
        inputs: list[BeautifyPlanInput] = []
        for item in items:
            reused_decision = beautify_plan_from_processing_json(
                getattr(item, "ai_processing_json", None)
            )
            if reused_decision is not None:
                try:
                    reused_plan = build_stored_plan(profile, reused_decision)
                except ValueError as exc:
                    emit_metric(
                        logger,
                        "beautify_plan_reuse_fallback_total",
                        labels={
                            "job_id": item.job_id,
                            "image_id": item.id,
                            "reason": "policy_validation",
                        },
                    )
                    logger.warning(
                        "Combined beautify plan failed policy validation; falling back "
                        "image_id=%s error=%s",
                        item.id,
                        exc,
                    )
                else:
                    saved = await repository.save_beautify_plan(
                        item.id,
                        status="completed",
                        model_name=(
                            getattr(item, "ai_processing_model", None)
                            or settings.ai_tagging_model
                        ),
                        prompt_version=(
                            getattr(item, "ai_processing_prompt_version", None)
                            or PROCESSING_PROMPT_VERSION
                        ),
                        duration_ms=0,
                        payload=reused_plan.model_dump(mode="json"),
                        error_message=None,
                    )
                    emit_metric(
                        logger,
                        "beautify_plan_reuse_total",
                        labels={
                            "job_id": item.job_id,
                            "image_id": item.id,
                            "saved": saved,
                        },
                    )
                    if saved:
                        RedactionDetectionTaskPublisher().publish(item.id)
                    continue
            try:
                image_bytes = await storage.download(
                    getattr(item, "processing_object_key", None) or item.object_key
                )
            except Exception:
                logger.exception("Unable to load image for beautify planning image_id=%s", item.id)
                await _persist_beautify_outcome(
                    repository,
                    item,
                    job,
                    settings,
                    profile,
                    BeautifyPlanOutcome(
                        status="failed",
                        error_message="美化规划文件读取失败",
                    ),
                )
                continue
            pending_items.append(item)
            inputs.append(
                BeautifyPlanInput(
                    image_bytes=image_bytes,
                    instruction=instruction,
                    image_context=_image_context(item),
                )
            )
        if not pending_items:
            return

        emit_metric(
            logger,
            "beautify_plan_requests_total",
            labels={
                "job_id": items[0].job_id,
                "batch_size": len(pending_items),
                "beautify_profile_id": job.beautify_profile_id,
                "beautify_profile_version": (job.beautify_profile_snapshot or {}).get("version"),
            },
        )
        outcomes = await BeautifyPlanningService(settings).analyze_many(
            inputs,
            fairness_key=items[0].job_id,
        )
        for item, outcome in zip(pending_items, outcomes, strict=True):
            await _persist_beautify_outcome(
                repository, item, job, settings, profile, outcome
            )


async def _persist_beautify_outcome(
    repository,
    item,
    job,
    settings,
    profile,
    outcome: BeautifyPlanOutcome,
) -> None:
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
            item,
            f"美化规划失败：{outcome.error_message or '模型响应无效'}",
            node="beautify_planning",
            code="UPSTREAM_UNAVAILABLE" if outcome.retryable else "INVALID_AI_RESPONSE",
            duration_ms=outcome.duration_ms,
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
        RedactionDetectionTaskPublisher().publish(item.id)


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
