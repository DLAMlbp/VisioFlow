from __future__ import annotations

import asyncio
import logging

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
from src.services.jobs.dispatch import RoutedProcessingTaskPublisher
from src.services.jobs.progression import advance_after_preprocess as _advance_after_preprocess
from src.services.managed_profiles import (
    redaction_from_snapshot,
    standard_with_global_filter,
    standards_from_snapshots,
)
from src.services.profiles import ProcessingStandard
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="image.classify_completion",
    queue="classification",
    max_retries=0,
)
def classify_completion(image_id: str) -> None:
    asyncio.run(_classify_completion(image_id))


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
        image_bytes = await get_storage_provider().download(
            getattr(item, "processing_object_key", None) or item.object_key
        )
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
                item,
                f"完工分类失败：{outcome.error_message or '模型响应无效'}",
                node="classification",
                code="UPSTREAM_UNAVAILABLE" if outcome.retryable else "INVALID_AI_RESPONSE",
                duration_ms=outcome.duration_ms,
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
    redaction_profile = redaction_from_snapshot(
        getattr(job, "redaction_profile_snapshot", None),
        legacy_beautify_snapshot=getattr(job, "beautify_profile_snapshot", None),
    )
    standards = standards_from_snapshots(job.processing_standard_snapshots)
    if not standards:
        await repository.fail_item(item, "任务缺少完整的分类过滤标准快照")
        await _advance_after_preprocess(repository, item)
        return
    if get_settings().combined_classify_filter_enabled:
        combined_standards = [
            standard_with_global_filter(standard, job.filter_profile_snapshot)
            for standard in standards
        ]
        await _classify_and_filter_standard(
            repository,
            item,
            job,
            settings,
            image_bytes,
            combined_standards,
        )
        return

    global_standard = _global_filter_standard(job.filter_profile_snapshot)
    if global_standard is None:
        await repository.fail_item(item, "任务缺少全局过滤标准快照")
        await _advance_after_preprocess(repository, item)
        return
    global_outcome = await ProcessingVisionService(settings).analyze(
        image_bytes,
        standards=[global_standard],
        unmatched_standard_policy="reject",
        image_context=_image_context(item),
        redaction_profile=redaction_profile,
    )
    emit_metric(
        logger,
        "global_filter_requests_total",
        labels={
            "job_id": item.job_id,
            "image_id": item.id,
            "status": global_outcome.status,
            "profile_id": global_standard.id,
        },
    )
    if global_outcome.status != "completed" or global_outcome.payload is None:
        await repository.save_completion_and_route(
            item,
            status="failed",
            model_name=settings.ai_tagging_model,
            prompt_version=PROCESSING_PROMPT_VERSION,
            duration_ms=global_outcome.duration_ms,
            payload=None,
            error_message=global_outcome.error_message,
        )
        await repository.fail_item(
            item,
            f"全局过滤失败：{global_outcome.error_message or '模型响应无效'}",
            node="filtering",
            code=(
                "UPSTREAM_UNAVAILABLE"
                if global_outcome.retryable
                else "INVALID_AI_RESPONSE"
            ),
            duration_ms=global_outcome.duration_ms,
        )
        await _advance_after_preprocess(repository, item)
        return

    global_payload = global_outcome.payload.model_dump(mode="json")
    ground_film = evaluate_ground_film(
        redaction_profile, global_outcome.payload.redaction_analysis
    )
    if ground_film.rejected:
        await repository.save_completion_and_route(
            item,
            status="completed",
            model_name=settings.ai_tagging_model,
            prompt_version=PROCESSING_PROMPT_VERSION,
            duration_ms=global_outcome.duration_ms,
            payload={"global_filter": global_payload},
            error_message=None,
        )
        await repository.reject_item(
            item,
            [RejectCode.BRANDED_GROUND_FILM_COVERAGE],
            reason=ground_film.reason,
        )
        await _advance_after_preprocess(repository, item)
        return
    if ground_film.review_required:
        await repository.mark_review_required(item.id)
    if global_outcome.payload.filter.rejected:
        await repository.save_completion_and_route(
            item,
            status="completed",
            model_name=settings.ai_tagging_model,
            prompt_version=PROCESSING_PROMPT_VERSION,
            duration_ms=global_outcome.duration_ms,
            payload={"global_filter": global_payload},
            error_message=None,
        )
        await repository.reject_item(
            item,
            [RejectCode.AI_FILTER_REJECTED],
            reason=precise_filter_reason(
                global_outcome.payload.filter,
                standard_name=global_standard.name or "全局过滤标准",
            ),
        )
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
        await repository.save_completion_and_route(
            item,
            status="failed",
            model_name=settings.ai_tagging_model,
            prompt_version=CLASSIFICATION_PROMPT_VERSION,
            duration_ms=(global_outcome.duration_ms or 0) + (outcome.duration_ms or 0),
            payload={"global_filter": global_payload},
            error_message=outcome.error_message,
        )
        await repository.fail_item(
            item,
            f"图片分类失败：{outcome.error_message or '模型响应无效'}",
            node="classification",
            code="UPSTREAM_UNAVAILABLE" if outcome.retryable else "INVALID_AI_RESPONSE",
            duration_ms=outcome.duration_ms,
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
        prompt_version=f"{PROCESSING_PROMPT_VERSION}+{CLASSIFICATION_PROMPT_VERSION}",
        duration_ms=(global_outcome.duration_ms or 0) + (outcome.duration_ms or 0),
        payload={
            "global_filter": global_payload,
            "normalized": outcome.payload.model_dump(mode="json"),
        },
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


def _global_filter_standard(snapshot: dict[str, object] | None) -> ProcessingStandard | None:
    if not snapshot:
        return None
    instruction = str(snapshot.get("instruction") or "").strip()
    if not instruction:
        return None
    version = snapshot.get("version")
    return ProcessingStandard(
        id=str(snapshot.get("id") or "global_filter"),
        name=str(snapshot.get("name") or "全局过滤标准"),
        version=int(version) if isinstance(version, int) else 1,
        description="所有图片进入分类前必须通过的全局过滤标准",
        classification_rule="所有图片始终命中全局过滤标准",
        filter_rule=instruction,
        priority=10000,
    )


async def _classify_and_filter_standard(
    repository,
    item,
    job,
    settings,
    image_bytes: bytes,
    standards,
    redaction_profile=None,
) -> None:
    redaction_profile = redaction_profile or redaction_from_snapshot(
        getattr(job, "redaction_profile_snapshot", None),
        legacy_beautify_snapshot=getattr(job, "beautify_profile_snapshot", None),
    )
    outcome = await ProcessingVisionService(settings).analyze(
        image_bytes,
        standards=standards,
        unmatched_standard_policy="reject",
        image_context=_image_context(item),
        redaction_profile=redaction_profile,
    )
    selection = outcome.payload.standard_selection if outcome.payload is not None else None
    emit_metric(
        logger,
        "combined_classify_filter_requests_total",
        labels={
            "job_id": item.job_id,
            "image_id": item.id,
            "status": outcome.status,
            "candidate_count": (outcome.diagnostic_json or {}).get("candidate_count"),
            "returned_candidate_index": (outcome.diagnostic_json or {}).get(
                "returned_candidate_index"
            ),
            "validation_result": (outcome.diagnostic_json or {}).get("validation_result"),
            "failure_kind": outcome.failure_kind,
            "selected_standard_id": (
                selection.selected_standard_id if selection is not None else None
            ),
        },
    )
    if outcome.status != "completed" or outcome.payload is None or selection is None:
        message = f"图片分类过滤失败：{outcome.error_message or '模型响应无效'}"
        await repository.fail_combined_classification_filter(
            item,
            model_name=settings.ai_tagging_model,
            completion_prompt_version=PROCESSING_PROMPT_VERSION,
            processing_prompt_version=PROCESSING_PROMPT_VERSION,
            duration_ms=outcome.duration_ms,
            diagnostic_json=outcome.diagnostic_json,
            error_message=message,
            code=outcome.failure_code,
        )
        await _advance_after_preprocess(repository, item)
        return

    selected_id = selection.selected_standard_id
    selected_standard = next(
        (standard for standard in standards if standard.id == selected_id),
        None,
    )
    selected_evaluation = next(
        (
            evaluation
            for evaluation in selection.evaluations
            if evaluation.standard_id == selected_id
        ),
        None,
    )
    snapshot = _route_snapshot_for_standard(job, selected_id or "")
    routed_id, routed_version = _snapshot_identity(snapshot)
    if selected_standard is None or selected_evaluation is None or not routed_id:
        await repository.fail_combined_classification_filter(
            item,
            model_name=settings.ai_tagging_model,
            completion_prompt_version=PROCESSING_PROMPT_VERSION,
            processing_prompt_version=PROCESSING_PROMPT_VERSION,
            duration_ms=outcome.duration_ms,
            diagnostic_json=outcome.diagnostic_json,
            error_message="分类结果对应的过滤标准快照不存在",
            code="INTERNAL_ERROR",
        )
        await _advance_after_preprocess(repository, item)
        return

    ground_film = evaluate_ground_film(
        redaction_profile, outcome.payload.redaction_analysis
    )
    passed = not outcome.payload.filter.rejected and not ground_film.rejected
    standard_name = selected_standard.name or selected_standard.description
    filter_reason = (
        ground_film.reason
        if ground_film.rejected
        else outcome.payload.filter.reason
        if passed
        else precise_filter_reason(outcome.payload.filter, standard_name=standard_name)
    )
    reasons = (
        [
            f"图片分类：{selection.reason}",
            f"后端路由：{standard_name}",
            f"AI 筛选：{outcome.payload.filter.reason}",
        ]
        if passed
        else [filter_reason]
    )
    completion_payload = {
        "normalized": {
            "evaluations": [
                evaluation.model_dump(mode="json")
                for evaluation in selection.evaluations
            ],
            "selected_standard_id": selected_id,
            "reason": selection.reason,
        }
    }
    processing_payload = outcome.payload.model_dump(mode="json")
    processing_payload["cover_score_version"] = COVER_SCORE_VERSION
    cover_score = calculate_cover_score(
        outcome.payload.cover_assessment,
        technical_score=_quality_score(item),
    )
    saved = await repository.complete_combined_classification_filter(
        item,
        model_name=settings.ai_tagging_model,
        completion_prompt_version=PROCESSING_PROMPT_VERSION,
        processing_prompt_version=PROCESSING_PROMPT_VERSION,
        duration_ms=outcome.duration_ms,
        completion_payload=completion_payload,
        processing_payload=processing_payload,
        diagnostic_json=outcome.diagnostic_json,
        routed_filter_profile_id=routed_id,
        routed_filter_profile_version=routed_version,
        completion_label=compatibility_route_label(routed_id),
        confidence=selected_evaluation.confidence,
        review_required=(
            selected_evaluation.confidence < settings.completion_review_confidence
            or ground_film.review_required
        ),
        passed=passed,
        final_score=cover_score,
        reasons=reasons,
        reject_codes=(
            []
            if passed
            else [RejectCode.BRANDED_GROUND_FILM_COVERAGE]
            if ground_film.rejected
            else [RejectCode.AI_FILTER_REJECTED]
        ),
    )
    if saved:
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
