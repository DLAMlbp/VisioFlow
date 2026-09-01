from __future__ import annotations

import asyncio
import logging

from celery import Task

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.hard_filter import RejectCode
from src.services.images.processing_vision import (
    PROCESSING_PROMPT_VERSION,
    ProcessingVisionService,
    compatibility_route_label,
    precise_filter_reason,
)
from src.services.images.quality import QualityEngine
from src.services.images.vision_rate_limit import retry_countdown
from src.services.managed_profiles import standards_from_snapshots
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app
from src.workers.preprocess import _advance_after_preprocess

logger = logging.getLogger(__name__)


class RoutedProcessingTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        image_id = args[0] if args else kwargs.get("image_id")
        if image_id:
            try:
                asyncio.run(_mark_processing_failed(image_id))
            except Exception:
                logger.exception("Unable to mark routed processing as failed")


@celery_app.task(
    bind=True,
    base=RoutedProcessingTask,
    name="image.apply_routed_processing",
    queue="filtering",
    max_retries=3,
    default_retry_delay=10,
)
def apply_routed_processing(task, image_id: str) -> None:
    try:
        asyncio.run(_apply_routed_processing(image_id))
    except Exception as exc:
        countdown = retry_countdown(get_settings(), task.request.retries)
        emit_metric(
            logger,
            "vision_task_retry_total",
            labels={"operation": "routed_filter", "image_id": image_id, "delay": countdown},
        )
        raise task.retry(exc=exc, countdown=countdown) from exc


async def _apply_routed_processing(image_id: str) -> None:
    if not get_settings().completion_routing_enabled:
        logger.error(
            "Completion routing is disabled; routed filter remains pending image_id=%s", image_id
        )
        return
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.claim_routed_processing(image_id)
        if item is None:
            return
        job = await repository.get_config(item.job_id)
        if job is None or job.cancel_requested_at is not None:
            return
        snapshot = _routed_snapshot(job, item.routed_filter_profile_id)
        standards = standards_from_snapshots([snapshot] if snapshot else [])
        if len(standards) != 1:
            await repository.fail_item(item, "路由过滤标准快照缺失或不合法")
            await _advance_after_preprocess(repository, item)
            return

        settings = load_ai_model_settings(get_settings())
        image_bytes = await get_storage_provider().download(item.object_key)
        emit_metric(
            logger,
            "routed_filter_requests_total",
            labels={
                "branch": item.completion_subtype,
                "job_id": item.job_id,
                "image_id": item.id,
                "filter_profile_id": item.routed_filter_profile_id,
                "filter_profile_version": item.routed_filter_profile_version,
            },
        )
        outcome = await ProcessingVisionService(settings).analyze(
            image_bytes,
            standards=standards,
            unmatched_standard_policy="reject",
            image_context=_image_context(item),
            route_label=(
                item.completion_label
                or compatibility_route_label(standards[0].id)
            ),
        )
        if outcome.status != "completed" or outcome.payload is None:
            if outcome.retryable:
                await repository.reset_routed_processing_for_retry(item.id)
                raise RuntimeError(outcome.error_message or "分支过滤调用失败")
            await repository.save_ai_processing(
                item,
                status="failed",
                model_name=settings.ai_tagging_model,
                prompt_version=PROCESSING_PROMPT_VERSION,
                duration_ms=outcome.duration_ms,
                payload=None,
                diagnostic_json=outcome.diagnostic_json,
                error_message=outcome.error_message,
            )
            await repository.fail_item(
                item, f"分支过滤失败：{outcome.error_message or '模型响应无效'}"
            )
            await _advance_after_preprocess(repository, item)
            return

        await repository.save_ai_processing(
            item,
            status="completed",
            model_name=settings.ai_tagging_model,
            prompt_version=PROCESSING_PROMPT_VERSION,
            duration_ms=outcome.duration_ms,
            payload=outcome.payload.model_dump(mode="json"),
            diagnostic_json=outcome.diagnostic_json,
            error_message=None,
        )
        if outcome.payload.filter.rejected:
            standard_name = standards[0].name or standards[0].description
            await repository.reject_item(
                item,
                [RejectCode.AI_FILTER_REJECTED],
                reason=precise_filter_reason(
                    outcome.payload.filter,
                    standard_name=standard_name,
                ),
            )
            await _advance_after_preprocess(repository, item)
            return

        final_score = _quality_score(item)
        completion_reason = _completion_reason(item.completion_json)
        await repository.complete_filter(
            item,
            final_score=final_score,
            reasons=[
                f"图片分类：{completion_reason}",
                f"后端路由：{standards[0].name or standards[0].description}",
                f"AI 筛选：{outcome.payload.filter.reason}",
            ],
        )
        await _advance_after_preprocess(repository, item)


async def _mark_processing_failed(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        if item is not None and item.status == "analyzing":
            await repository.fail_item(item, "分支过滤任务多次重试后仍失败")
            await _advance_after_preprocess(repository, item)


def _routed_snapshot(job, profile_id: str | None) -> dict[str, object] | None:
    for snapshot in job.processing_standard_snapshots or []:
        if str(snapshot.get("id") or "") == profile_id:
            return snapshot
    if profile_id == job.completed_filter_profile_id:
        return job.completed_filter_profile_snapshot
    if profile_id == job.non_completed_filter_profile_id:
        return job.non_completed_filter_profile_snapshot
    return None


def _image_context(item) -> dict[str, int | float]:
    metric = item.metric
    values: dict[str, int | float] = {
        "width": int(item.width or 0),
        "height": int(item.height or 0),
    }
    if metric is not None:
        values.update(
            {
                "sharpness_score": float(metric.sharpness_score),
                "exposure_score": float(metric.exposure_score),
                "contrast_score": float(metric.contrast_score),
                "noise_score": float(metric.noise_score),
            }
        )
        values["quality_score"] = _quality_score(item)
    return values


def _quality_score(item) -> float:
    metric = item.metric
    if metric is None:
        return 0.0
    return QualityEngine.calculate_weighted_quality_score(
        sharpness=float(metric.sharpness_score),
        exposure=float(metric.exposure_score),
        contrast=float(metric.contrast_score),
        noise=float(metric.noise_score),
    )


def _completion_reason(payload: object) -> str:
    if isinstance(payload, dict):
        normalized = payload.get("normalized")
        if isinstance(normalized, dict) and normalized.get("reason"):
            return str(normalized["reason"])
    return "已按可见事实完成分类"
