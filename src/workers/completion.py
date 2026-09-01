from __future__ import annotations

import asyncio
import logging

from celery import Task

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.classification import (
    CLASSIFICATION_PROMPT_VERSION,
    StandardClassificationVisionService,
)
from src.services.images.completion import (
    COMPLETION_PROMPT_VERSION,
    CompletionVisionService,
)
from src.services.images.processing_vision import compatibility_route_label
from src.services.images.vision_rate_limit import retry_countdown
from src.services.jobs.dispatch import RoutedProcessingTaskPublisher
from src.services.managed_profiles import standards_from_snapshots
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app
from src.workers.preprocess import _advance_after_preprocess

logger = logging.getLogger(__name__)


class CompletionTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        image_id = args[0] if args else kwargs.get("image_id")
        if image_id:
            try:
                asyncio.run(_mark_completion_failed(image_id))
            except Exception:
                logger.exception("Unable to mark completion classification as failed")


@celery_app.task(
    bind=True,
    base=CompletionTask,
    name="image.classify_completion",
    queue="classification",
    max_retries=3,
    default_retry_delay=10,
)
def classify_completion(task, image_id: str) -> None:
    try:
        asyncio.run(_classify_completion(image_id))
    except Exception as exc:
        countdown = retry_countdown(get_settings(), task.request.retries)
        emit_metric(
            logger,
            "vision_task_retry_total",
            labels={"operation": "classification", "image_id": image_id, "delay": countdown},
        )
        raise task.retry(exc=exc, countdown=countdown) from exc


async def _classify_completion(image_id: str) -> None:
    if not get_settings().completion_routing_enabled:
        logger.error("Completion routing is disabled; image remains pending image_id=%s", image_id)
        return
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.claim_completion(image_id)
        if item is None:
            return
        job = await repository.get_config(item.job_id)
        if job is None or job.cancel_requested_at is not None:
            return
        settings = load_ai_model_settings(get_settings())
        image_bytes = await get_storage_provider().download(item.object_key)
        if job.routing_mode in {"standards", "streaming_v2"}:
            await _classify_filter_standard(
                repository, item, job, settings, image_bytes
            )
            return
        instruction = _snapshot_instruction(
            job.completion_profile_snapshot,
            "逐图判断真实室内装修空间属于完工或非完工",
        )
        outcome = await CompletionVisionService(settings).analyze(
            image_bytes, instruction=instruction
        )
        emit_metric(
            logger,
            "completion_requests_total",
            labels={
                "job_id": item.job_id,
                "image_id": item.id,
                "status": outcome.status,
                "subtype": (outcome.decision.subtype if outcome.decision is not None else None),
                "profile_id": job.completion_profile_id,
                "profile_version": (job.completion_profile_snapshot or {}).get("version"),
            },
        )
        if outcome.status != "completed" or outcome.decision is None:
            if outcome.retryable:
                await repository.reset_completion_for_retry(item.id)
                raise RuntimeError(outcome.error_message or "完工分类调用失败")
            await repository.save_completion_and_route(
                item,
                status="failed",
                model_name=settings.ai_tagging_model,
                prompt_version=COMPLETION_PROMPT_VERSION,
                duration_ms=outcome.duration_ms,
                payload=None,
                error_message=outcome.error_message,
            )
            await repository.fail_item(
                item, f"完工分类失败：{outcome.error_message or '模型响应无效'}"
            )
            await _advance_after_preprocess(repository, item)
            return

        decision = outcome.decision
        routed_id, routed_version = _snapshot_identity(
            _route_snapshot_for_label(job, decision.label)
        )

        if not routed_id:
            await repository.fail_item(item, "任务缺少分类对应的过滤标准快照")
            await _advance_after_preprocess(repository, item)
            return

        audit_payload = {
            "model": outcome.model_payload.model_dump(mode="json")
            if outcome.model_payload is not None
            else None,
            "normalized": decision.model_dump(mode="json"),
        }
        saved = await repository.save_completion_and_route(
            item,
            status="completed",
            model_name=settings.ai_tagging_model,
            prompt_version=COMPLETION_PROMPT_VERSION,
            duration_ms=outcome.duration_ms,
            payload=audit_payload,
            error_message=None,
            label=decision.label,
            subtype=decision.subtype,
            confidence=decision.confidence,
            review_required=decision.review_required,
            routed_filter_profile_id=routed_id,
            routed_filter_profile_version=routed_version,
        )
        if not saved:
            return
        RoutedProcessingTaskPublisher().publish(item.id)


async def _classify_filter_standard(repository, item, job, settings, image_bytes: bytes) -> None:
    standards = standards_from_snapshots(job.processing_standard_snapshots)
    if not standards:
        await repository.fail_item(item, "任务缺少完整的分类过滤标准快照")
        await _advance_after_preprocess(repository, item)
        return
    outcome = await StandardClassificationVisionService(settings).analyze(
        image_bytes,
        standards=standards,
    )
    emit_metric(
        logger,
        "filter_classification_requests_total",
        labels={
            "job_id": item.job_id,
            "image_id": item.id,
            "status": outcome.status,
            "selected_standard_id": (
                outcome.payload.selected_standard_id if outcome.payload is not None else None
            ),
        },
    )
    if outcome.status != "completed" or outcome.payload is None or outcome.selected is None:
        if outcome.retryable:
            await repository.reset_completion_for_retry(item.id)
            raise RuntimeError(outcome.error_message or "图片分类调用失败")
        await repository.save_completion_and_route(
            item,
            status="failed",
            model_name=settings.ai_tagging_model,
            prompt_version=CLASSIFICATION_PROMPT_VERSION,
            duration_ms=outcome.duration_ms,
            payload=None,
            error_message=outcome.error_message,
        )
        await repository.fail_item(
            item, f"图片分类失败：{outcome.error_message or '模型响应无效'}"
        )
        await _advance_after_preprocess(repository, item)
        return

    snapshot = _route_snapshot_for_standard(job, outcome.payload.selected_standard_id)
    routed_id, routed_version = _snapshot_identity(snapshot)
    if not routed_id:
        await repository.fail_item(item, "分类结果对应的过滤标准快照不存在")
        await _advance_after_preprocess(repository, item)
        return

    saved = await repository.save_completion_and_route(
        item,
        status="completed",
        model_name=settings.ai_tagging_model,
        prompt_version=CLASSIFICATION_PROMPT_VERSION,
        duration_ms=outcome.duration_ms,
        payload={"normalized": outcome.payload.model_dump(mode="json")},
        error_message=None,
        label=compatibility_route_label(routed_id),
        confidence=outcome.selected.confidence,
        review_required=(
            outcome.selected.confidence < settings.completion_review_confidence
        ),
        routed_filter_profile_id=routed_id,
        routed_filter_profile_version=routed_version,
    )
    if saved:
        RoutedProcessingTaskPublisher().publish(item.id)


async def _mark_completion_failed(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        if item is not None and item.status == "analyzing":
            await repository.fail_item(item, "完工分类任务多次重试后仍失败")
            await _advance_after_preprocess(repository, item)


def _snapshot_instruction(snapshot: dict[str, object] | None, fallback: str) -> str:
    instruction = (snapshot or {}).get("instruction")
    return str(instruction).strip() if instruction else fallback


def _snapshot_identity(snapshot: dict[str, object] | None) -> tuple[str | None, int | None]:
    if not snapshot:
        return None, None
    profile_id = snapshot.get("id")
    version = snapshot.get("version")
    return (
        str(profile_id) if profile_id else None,
        int(version) if isinstance(version, int) else None,
    )


def _route_snapshot_for_label(job, label: str) -> dict[str, object] | None:
    if label == "completed":
        return job.completed_filter_profile_snapshot
    return job.non_completed_filter_profile_snapshot


def _route_snapshot_for_standard(job, standard_id: str) -> dict[str, object] | None:
    for snapshot in job.processing_standard_snapshots or []:
        if str(snapshot.get("id") or "") == standard_id:
            return snapshot
    return None
