from __future__ import annotations

import asyncio
import logging

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.cover_score import COVER_SCORE_VERSION, calculate_cover_score
from src.services.images.hard_filter import RejectCode
from src.services.images.processing_vision import (
    PROCESSING_PROMPT_VERSION,
    ProcessingVisionService,
    compatibility_route_label,
    precise_filter_reason,
)
from src.services.images.quality import QualityEngine
from src.services.images.redaction_policy import evaluate_ground_film
from src.services.jobs.progression import advance_after_preprocess as _advance_after_preprocess
from src.services.managed_profiles import redaction_from_snapshot, standards_from_snapshots
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="image.apply_routed_processing",
    queue="filtering",
    max_retries=0,
)
def apply_routed_processing(image_id: str) -> None:
    asyncio.run(_apply_routed_processing(image_id))


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
        redaction_profile = redaction_from_snapshot(
            getattr(job, "redaction_profile_snapshot", None),
            legacy_beautify_snapshot=getattr(job, "beautify_profile_snapshot", None),
        )
        image_bytes = await get_storage_provider().download(
            getattr(item, "processing_object_key", None) or item.object_key
        )
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
            redaction_profile=redaction_profile,
        )
        if outcome.status != "completed" or outcome.payload is None:
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
                item,
                f"分支过滤失败：{outcome.error_message or '模型响应无效'}",
                node="filtering",
                code="UPSTREAM_UNAVAILABLE" if outcome.retryable else "INVALID_AI_RESPONSE",
                duration_ms=outcome.duration_ms,
            )
            await _advance_after_preprocess(repository, item)
            return

        processing_payload = outcome.payload.model_dump(mode="json")
        processing_payload["cover_score_version"] = COVER_SCORE_VERSION
        await repository.save_ai_processing(
            item,
            status="completed",
            model_name=settings.ai_tagging_model,
            prompt_version=PROCESSING_PROMPT_VERSION,
            duration_ms=outcome.duration_ms,
            payload=processing_payload,
            diagnostic_json=outcome.diagnostic_json,
            error_message=None,
        )
        ground_film = evaluate_ground_film(
            redaction_profile, outcome.payload.redaction_analysis
        )
        if ground_film.rejected:
            await repository.reject_item(
                item,
                [RejectCode.BRANDED_GROUND_FILM_COVERAGE],
                reason=ground_film.reason,
            )
            await _advance_after_preprocess(repository, item)
            return
        if ground_film.review_required:
            await repository.mark_review_required(item.id)
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

        final_score = calculate_cover_score(
            outcome.payload.cover_assessment,
            technical_score=_quality_score(item),
        )
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
