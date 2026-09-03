from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import selectinload

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.models.image_item import ImageItem
from src.models.image_job import ImageJob
from src.models.upload_batch import UploadBatch, UploadBatchItem
from src.repositories.jobs import ImageJobRepository
from src.services.jobs.dispatch import (
    AnalysisTaskPublisher,
    BeautifyPlanTaskPublisher,
    CompletionTaskPublisher,
    EmbeddingTaskPublisher,
    EnhancementTaskPublisher,
    InpaintTaskPublisher,
    JobDispatchTaskPublisher,
    MatchTaskPublisher,
    MetadataTaskPublisher,
    RankingTaskPublisher,
    RedactionDetectionTaskPublisher,
    RenderTaskPublisher,
    RoutedProcessingTaskPublisher,
    release_recovery_lease,
)
from src.services.storage.factory import get_storage_provider
from src.services.storage.keys import (
    build_enhancement_work_object_key,
    build_redaction_base_object_key,
)
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_POST_FILTER_ACTIVE_STATUSES = (
    "filtered",
    "beautify_planning",
    "enhancing",
    "enhanced",
    "tagging",
)


@celery_app.task(name="maintenance.cleanup_expired_images", queue="cleanup", max_retries=0)
def cleanup_expired_images() -> None:
    asyncio.run(_cleanup_expired_images())


async def _cleanup_expired_images() -> None:
    settings = get_settings()
    storage = get_storage_provider()
    cutoff = datetime.now(UTC) - timedelta(days=settings.image_retention_days)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ImageItem)
            .join(ImageJob, ImageJob.id == ImageItem.job_id)
            .where(
                ImageJob.completed_at.is_not(None),
                ImageJob.completed_at < cutoff,
                ImageItem.purged_at.is_(None),
            )
            .options(selectinload(ImageItem.result))
            .limit(100)
        )
        for item in result.scalars().unique():
            keys = [
                item.object_key,
                item.thumbnail_object_key,
                item.analysis_object_key,
                build_redaction_base_object_key(item.job_id, item.id),
                *[
                    build_enhancement_work_object_key(item.job_id, item.id, stage, extension)
                    for stage, extension in (
                        ("normalized", "jpg"),
                        ("watermark-mask", "png"),
                        ("watermark", "jpg"),
                        ("beautified", "jpg"),
                        ("state", "json"),
                    )
                ],
            ]
            if item.result is not None:
                keys.append(item.result.enhanced_object_key)
            for object_key in dict.fromkeys(key for key in keys if key):
                try:
                    await storage.delete(object_key)
                except Exception:
                    logger.warning("Unable to delete expired object %s", object_key, exc_info=True)
            item.purged_at = datetime.now(UTC)
        await session.commit()

        batches = await session.execute(
            select(UploadBatch)
            .where(
                UploadBatch.status.in_(("registered", "completed")),
                UploadBatch.expires_at < datetime.now(UTC),
                UploadBatch.items.any(UploadBatchItem.status.in_(("registered", "abandoned"))),
            )
            .options(selectinload(UploadBatch.items))
            .limit(20)
        )
        for batch in batches.scalars().unique():
            for item in batch.items:
                if item.status not in {"registered", "abandoned"}:
                    continue
                try:
                    await storage.delete(item.object_key)
                except Exception:
                    logger.debug(
                        "Unable to delete orphan upload %s", item.object_key, exc_info=True
                    )
                item.status = "expired"
            if batch.status == "registered":
                batch.status = "expired"
        await session.commit()


@celery_app.task(name="maintenance.recover_stalled_images", queue="cleanup", max_retries=0)
def recover_stalled_images() -> None:
    asyncio.run(_recover_stalled_images())


async def _recover_stalled_images() -> None:
    """Recover bounded idempotent stages and fail terminally stalled work."""
    settings = get_settings()
    now = datetime.now(UTC)
    active_statuses = ("queued", "processing", "ranking", "analyzing", "enhancing", "tagging")
    terminal_items = ("selected", "rejected", "failed", "not_selected", "cancelled")
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        jobs = list(
            (
                await session.execute(
                    select(ImageJob)
                    .where(
                        ImageJob.status.in_(active_statuses),
                        ImageJob.cancel_requested_at.is_(None),
                    )
                    .order_by(ImageJob.created_at)
                    .limit(100)
                )
            ).scalars()
        )
        for job in jobs:
            if job.deadline_at is not None and job.deadline_at <= now:
                await repository.fail_job(
                    job.id,
                    node="job_deadline",
                    code="JOB_TIMEOUT",
                    reason="整任务执行超过允许时限，已停止后续处理",
                    duration_ms=int((now - job.created_at).total_seconds() * 1000),
                )
                continue
            item = (
                await session.execute(
                    select(ImageItem)
                    .where(
                        ImageItem.job_id == job.id,
                        ImageItem.status.not_in(terminal_items),
                    )
                    .order_by(ImageItem.updated_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
            stalled = _stalled_node(item, job, now, settings) if item is not None else None
            if stalled is None:
                continue
            node, started_at, timeout_seconds = stalled
            if item is not None and item.status == "enhancing":
                stage = str(item.enhancement_stage or "")
                publisher = _enhancement_recovery_publisher(stage)
                if publisher is not None:
                    attempt = await repository.recover_enhancement_stage(
                        item.id,
                        expected_stage=stage,
                        started_at=started_at,
                        max_attempts=settings.pipeline_enhancement_max_recovery_attempts,
                    )
                    if attempt is not None:
                        publisher_name = type(publisher).__name__
                        release_recovery_lease(publisher_name, item.id)
                        try:
                            publisher.publish(item.id)
                        except Exception:
                            await repository.restore_enhancement_recovery_after_publish_failure(
                                item.id,
                                expected_stage=stage,
                                started_at=started_at,
                                recovery_attempt=attempt,
                            )
                            raise
                        emit_metric(
                            logger,
                            "enhancement_stage_recovery_total",
                            labels={
                                "job_id": job.id,
                                "image_id": item.id,
                                "stage": stage,
                                "attempt": attempt,
                            },
                        )
                        continue
                    exhausted = await repository.claim_exhausted_enhancement_failure(
                        item.id,
                        expected_stage=stage,
                        started_at=started_at,
                        max_attempts=settings.pipeline_enhancement_max_recovery_attempts,
                    )
                    if not exhausted:
                        # The worker advanced or completed this stage after it
                        # was selected by cleanup; leave the current state intact.
                        continue
            await repository.fail_job(
                job.id,
                node=node,
                code=(
                    "ENHANCEMENT_RECOVERY_EXHAUSTED"
                    if item is not None and item.status == "enhancing"
                    else "NODE_TIMEOUT"
                ),
                reason=f"节点 {node} 超过 {timeout_seconds} 秒未完成，任务已停止",
                image_id=item.id if item is not None else None,
                duration_ms=int((now - started_at).total_seconds() * 1000),
            )


def _stalled_node(item, job, now, settings):
    normal = settings.pipeline_task_timeout_seconds
    ai = settings.pipeline_ai_timeout_seconds

    def expired(node: str, started_at, seconds: int):
        if started_at is not None and (now - started_at).total_seconds() >= seconds:
            return node, started_at, seconds
        return None

    if job.status == "ranking":
        return expired("ranking", job.updated_at, normal)
    if item.status == "queued":
        return None
    if item.status == "analyzing":
        if item.completion_status == "processing":
            return expired(
                "classification", item.completion_started_at or item.updated_at, ai
            )
        if item.ai_processing_status == "processing":
            return expired("filtering", item.ai_processing_started_at or item.updated_at, ai)
        if item.completion_status == "pending" or item.ai_processing_status == "pending":
            return None
        return expired("preprocess", item.preprocess_started_at or item.updated_at, normal)
    if item.status == "beautify_planning" or item.beautify_plan_status == "processing":
        return expired(
            "beautify_planning", item.beautify_plan_started_at or item.updated_at, ai
        )
    if item.beautify_plan_status == "pending":
        return None
    if item.status == "enhancing":
        stage = str(item.enhancement_stage or "enhance")
        timeout = settings.pipeline_inpaint_timeout_seconds if stage == "inpaint" else normal
        return expired(stage, item.enhancement_stage_started_at, timeout)
    if item.analysis_status == "processing":
        return expired("content_analysis", item.analysis_started_at or item.updated_at, ai)
    if item.analysis_status == "pending":
        return None
    if item.embedding_status == "processing":
        return expired("embedding", item.embedding_started_at or item.updated_at, normal)
    if item.embedding_status == "pending":
        return None
    if item.match_status == "processing":
        return expired("matching", item.match_started_at or item.updated_at, normal)
    if item.match_status in {"pending", "queued"}:
        return None
    return expired("pipeline", item.updated_at, normal)


def _enhancement_recovery_publisher(stage: str):
    publisher_type = {
        "redaction": RedactionDetectionTaskPublisher,
        "inpaint": InpaintTaskPublisher,
        "enhance": EnhancementTaskPublisher,
        "render": RenderTaskPublisher,
    }.get(stage)
    return publisher_type() if publisher_type is not None else None


async def _recover_stalled_images_legacy() -> None:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.pipeline_stale_seconds)
    async with AsyncSessionLocal() as session:
        undispatched_jobs = list(
            (
                await session.execute(
                    select(ImageJob.id).where(
                        ImageJob.cancel_requested_at.is_(None),
                        ImageJob.status.in_(
                            ("queued", "processing", "ranking", "analyzing", "enhancing", "tagging")
                        ),
                        ImageJob.dispatch_cursor < ImageJob.total_count,
                        ImageJob.created_at < cutoff,
                    )
                )
            ).scalars()
        )
        queued = list(
            (
                await session.execute(
                    update(ImageItem)
                    .where(
                        ImageItem.status == "queued",
                        ImageItem.preprocess_dispatched_at.is_not(None),
                        ImageItem.preprocess_dispatched_at < cutoff,
                    )
                    .values(preprocess_dispatched_at=func.now())
                    .returning(ImageItem.id)
                )
            ).scalars()
        )
        preprocessing = list(
            (
                await session.execute(
                    update(ImageItem)
                    .where(
                        ImageItem.status == "analyzing",
                        ImageItem.preprocess_completed_at.is_(None),
                        ImageItem.preprocess_started_at < cutoff,
                    )
                    .values(status="queued", preprocess_started_at=None)
                    .returning(ImageItem.id)
                )
            ).scalars()
        )
        ranking_jobs = list(
            (
                await session.execute(
                    select(ImageJob.id).where(
                        ImageJob.cancel_requested_at.is_(None),
                        ImageJob.status == "ranking",
                        ImageJob.updated_at < cutoff,
                    )
                )
            ).scalars()
        )
        classifying = list(
            (
                await session.execute(
                    update(ImageItem)
                    .where(
                        ImageItem.status == "analyzing",
                        ImageItem.completion_status == "processing",
                        ImageItem.completion_started_at < cutoff,
                    )
                    .values(completion_status="pending", completion_started_at=None)
                    .returning(ImageItem.id)
                )
            ).scalars()
        )
        routed_processing = list(
            (
                await session.execute(
                    update(ImageItem)
                    .where(
                        ImageItem.status == "analyzing",
                        ImageItem.ai_processing_status == "processing",
                        ImageItem.ai_processing_started_at < cutoff,
                    )
                    .values(ai_processing_status="pending", ai_processing_started_at=None)
                    .returning(ImageItem.id)
                )
            ).scalars()
        )
        pending_completion = list(
            (
                await session.execute(
                    select(ImageItem.id).where(
                        ImageItem.status == "analyzing",
                        ImageItem.completion_status == "pending",
                        ImageItem.updated_at < cutoff,
                    )
                )
            ).scalars()
        )
        pending_routed_processing = list(
            (
                await session.execute(
                    select(ImageItem.id).where(
                        ImageItem.status == "analyzing",
                        ImageItem.completion_status == "completed",
                        ImageItem.ai_processing_status == "pending",
                        ImageItem.updated_at < cutoff,
                    )
                )
            ).scalars()
        )
        beautify_planning = list(
            (
                await session.execute(
                    update(ImageItem)
                    .where(
                        ImageItem.status == "beautify_planning",
                        ImageItem.beautify_plan_status == "processing",
                        ImageItem.beautify_plan_started_at < cutoff,
                    )
                    .values(
                        status="filtered",
                        beautify_plan_status="pending",
                        beautify_plan_started_at=None,
                    )
                    .returning(ImageItem.id)
                )
            ).scalars()
        )
        pending_beautify_plans = list(
            (
                await session.execute(
                    select(ImageItem.id).where(
                        ImageItem.status == "filtered",
                        ImageItem.beautify_plan_status == "pending",
                        ImageItem.updated_at < cutoff,
                    )
                )
            ).scalars()
        )
        unqueued_streaming_beautify = list(
            (
                await session.execute(
                    select(ImageItem.id)
                    .join(ImageJob, ImageJob.id == ImageItem.job_id)
                    .where(
                        ImageJob.routing_mode == "streaming_v2",
                        ImageJob.cancel_requested_at.is_(None),
                        ImageItem.status == "filtered",
                        ImageItem.beautify_plan_status.is_(None),
                        ImageItem.updated_at < cutoff,
                    )
                )
            ).scalars()
        )
        completed_beautify_plans = list(
            (
                await session.execute(
                    select(ImageItem.id).where(
                        ImageItem.status == "filtered",
                        ImageItem.beautify_plan_status == "completed",
                        ImageItem.enhance_started_at.is_(None),
                        ImageItem.updated_at < cutoff,
                    )
                )
            ).scalars()
        )
        legacy_enhancing = list(
            (
                await session.execute(
                    update(ImageItem)
                    .where(
                        ImageItem.status == "enhancing",
                        ImageItem.enhancement_stage.is_(None),
                        or_(
                            ImageItem.enhance_started_at < cutoff,
                            and_(
                                ImageItem.enhance_started_at.is_(None),
                                ImageItem.updated_at < cutoff,
                            ),
                        ),
                    )
                    .values(
                        status="filtered",
                        enhance_started_at=None,
                        enhancement_stage_started_at=None,
                    )
                    .returning(ImageItem.id)
                )
            ).scalars()
        )
        redaction = await _recover_enhancement_stage(session, "redaction", cutoff)
        inpaint = await _recover_enhancement_stage(session, "inpaint", cutoff)
        enhancement = await _recover_enhancement_stage(session, "enhance", cutoff)
        render = await _recover_enhancement_stage(session, "render", cutoff)
        pending_analysis = list(
            (
                await session.execute(
                    select(ImageItem.id).where(
                        ImageItem.status.in_(_POST_FILTER_ACTIVE_STATUSES),
                        ImageItem.analysis_status == "pending",
                        ImageItem.updated_at < cutoff,
                    )
                )
            ).scalars()
        )
        analyzing = await _reset_substage(
            session,
            column=ImageItem.analysis_status,
            timestamp=ImageItem.analysis_started_at,
            cutoff=cutoff,
        )
        pending_embeddings = list(
            (
                await session.execute(
                    select(ImageItem.id).where(
                        ImageItem.status.in_(_POST_FILTER_ACTIVE_STATUSES),
                        ImageItem.embedding_status == "pending",
                        ImageItem.updated_at < cutoff,
                    )
                )
            ).scalars()
        )
        embedding = await _reset_substage(
            session,
            column=ImageItem.embedding_status,
            timestamp=ImageItem.embedding_started_at,
            cutoff=cutoff,
        )
        provisional_embeddings = list(
            (
                await session.execute(
                    update(ImageItem)
                    .where(
                        ImageItem.status == "enhanced",
                        ImageItem.embedding_status.in_(
                            ("provisional", "provisional_failed")
                        ),
                        ImageItem.match_status == "pending",
                        ImageItem.updated_at < cutoff,
                    )
                    .values(
                        embedding=None,
                        embedding_version=None,
                        embedding_status="pending",
                        embedding_started_at=None,
                        embedding_completed_at=None,
                    )
                    .returning(ImageItem.id)
                )
            ).scalars()
        )
        matching = await _reset_substage(
            session,
            column=ImageItem.match_status,
            timestamp=ImageItem.match_started_at,
            cutoff=cutoff,
            reset_to="queued",
        )
        queued_matches = list(
            (
                await session.execute(
                    select(ImageItem.id).where(
                        ImageItem.status.in_(_POST_FILTER_ACTIVE_STATUSES),
                        ImageItem.match_status == "queued",
                        ImageItem.updated_at < cutoff,
                    )
                )
            ).scalars()
        )
        ready_matches = list(
            (
                await session.execute(
                    select(ImageItem.id).where(
                        ImageItem.status.in_(_POST_FILTER_ACTIVE_STATUSES),
                        ImageItem.match_status == "pending",
                        ImageItem.analysis_status.in_(("completed", "failed")),
                        ImageItem.embedding_status.in_(("completed", "failed")),
                        ImageItem.updated_at < cutoff,
                    )
                )
            ).scalars()
        )
        completed_matches = list(
            (
                await session.execute(
                    select(ImageItem.id).where(
                        ImageItem.status.in_(_POST_FILTER_ACTIVE_STATUSES),
                        ImageItem.match_status == "completed",
                        ImageItem.updated_at < cutoff,
                    )
                )
            ).scalars()
        )
        await session.commit()

        repository = ImageJobRepository(session)
        barrier_candidates = list(
            (
                await session.execute(
                    select(ImageJob.id).where(
                        ImageJob.cancel_requested_at.is_(None),
                        ImageJob.routing_mode != "streaming_v2",
                        ImageJob.status == "processing",
                        ~ImageJob.items.any(ImageItem.status.in_(("queued", "analyzing"))),
                        ImageJob.items.any(ImageItem.status == "filtered"),
                    )
                )
            ).scalars()
        )
        barrier_ranking_jobs = [
            job_id
            for job_id in barrier_candidates
            if await repository.claim_ranking_if_filtering_complete(job_id)
        ]
        recovered_streaming_beautify: list[str] = []
        recovered_analysis: list[str] = []
        recovered_embedding: list[str] = []
        for image_id in unqueued_streaming_beautify:
            item = await repository.get_item(image_id)
            job = await repository.get_config(item.job_id) if item is not None else None
            if job is None:
                continue
            if get_settings().early_semantic_branch_enabled:
                queued = await repository.queue_post_filter_branches(
                    image_id,
                    similarity_enabled=job.similarity_enabled,
                )
                if queued and job.similarity_enabled:
                    recovered_analysis.append(image_id)
                    recovered_embedding.append(image_id)
            else:
                queued = await repository.queue_beautify_plan(image_id)
            if queued:
                recovered_streaming_beautify.append(image_id)
        newly_queued_matches = [
            image_id
            for image_id in ready_matches
            if await repository.claim_match_if_ready(image_id)
        ]
        for image_id in completed_matches:
            item = await repository.get_item(image_id)
            if item is None or item.similarity_match is None:
                continue
            if get_settings().early_semantic_branch_enabled:
                await repository.finalize_selected_if_ready(
                    item,
                    reason=item.similarity_match.message,
                )
            else:
                await repository.complete_tagging(
                    item,
                    reason=item.similarity_match.message,
                )

    _publish_pipeline_recovery(
        undispatched_jobs=undispatched_jobs,
        metadata=[*queued, *preprocessing],
        completion=[*classifying, *pending_completion],
        routed_processing=[*routed_processing, *pending_routed_processing],
        ranking=[*ranking_jobs, *barrier_ranking_jobs],
        beautify_plan=[
            *beautify_planning,
            *pending_beautify_plans,
            *recovered_streaming_beautify,
        ],
        redaction=[*legacy_enhancing, *completed_beautify_plans, *redaction],
        inpaint=inpaint,
        enhancement=enhancement,
        render=render,
        analysis=[*analyzing, *pending_analysis, *recovered_analysis],
        embedding=[
            *embedding,
            *pending_embeddings,
            *provisional_embeddings,
            *recovered_embedding,
        ],
        matching=[*matching, *queued_matches, *newly_queued_matches],
    )


def _publish_pipeline_recovery(
    *,
    undispatched_jobs: list[str],
    metadata: list[str],
    completion: list[str],
    routed_processing: list[str],
    ranking: list[str],
    beautify_plan: list[str],
    redaction: list[str],
    inpaint: list[str],
    enhancement: list[str],
    render: list[str],
    analysis: list[str],
    embedding: list[str],
    matching: list[str],
) -> None:
    _publish_many(JobDispatchTaskPublisher(), list(dict.fromkeys(undispatched_jobs)))
    _publish_many(MetadataTaskPublisher(), list(dict.fromkeys(metadata)))
    _publish_many(CompletionTaskPublisher(), list(dict.fromkeys(completion)))
    _publish_many(RoutedProcessingTaskPublisher(), list(dict.fromkeys(routed_processing)))
    _publish_many(RankingTaskPublisher(), list(dict.fromkeys(ranking)))
    _publish_many(BeautifyPlanTaskPublisher(), list(dict.fromkeys(beautify_plan)))
    _publish_many(RedactionDetectionTaskPublisher(), list(dict.fromkeys(redaction)))
    _publish_many(InpaintTaskPublisher(), list(dict.fromkeys(inpaint)))
    _publish_many(EnhancementTaskPublisher(), list(dict.fromkeys(enhancement)))
    _publish_many(RenderTaskPublisher(), list(dict.fromkeys(render)))
    _publish_many(AnalysisTaskPublisher(), list(dict.fromkeys(analysis)))
    _publish_many(EmbeddingTaskPublisher(), list(dict.fromkeys(embedding)))
    _publish_many(MatchTaskPublisher(), list(dict.fromkeys(matching)))


async def _recover_enhancement_stage(session, stage: str, cutoff) -> list[str]:
    stalled = list(
        (
            await session.execute(
                update(ImageItem)
                .where(
                    ImageItem.status == "enhancing",
                    ImageItem.enhancement_stage == stage,
                    ImageItem.enhancement_stage_started_at < cutoff,
                )
                .values(enhancement_stage_started_at=None)
                .returning(ImageItem.id)
            )
        ).scalars()
    )
    pending = list(
        (
            await session.execute(
                select(ImageItem.id).where(
                    ImageItem.status == "enhancing",
                    ImageItem.enhancement_stage == stage,
                    ImageItem.enhancement_stage_started_at.is_(None),
                    ImageItem.updated_at < cutoff,
                )
            )
        ).scalars()
    )
    return list(dict.fromkeys([*stalled, *pending]))


async def _reset_stage(session, *, status, timestamp, cutoff, values) -> list[str]:
    return list(
        (
            await session.execute(
                update(ImageItem)
                .where(ImageItem.status == status, timestamp < cutoff)
                .values(**values)
                .returning(ImageItem.id)
            )
        ).scalars()
    )


async def _reset_substage(
    session,
    *,
    column,
    timestamp,
    cutoff,
    reset_to: str = "pending",
    extra_condition=None,
) -> list[str]:
    filters = [
        ImageItem.status.in_(_POST_FILTER_ACTIVE_STATUSES),
        column == "processing",
        timestamp < cutoff,
    ]
    if extra_condition is not None:
        filters.append(extra_condition)
    return list(
        (
            await session.execute(
                update(ImageItem)
                .where(*filters)
                .values({column.key: reset_to, timestamp.key: None})
                .returning(ImageItem.id)
            )
        ).scalars()
    )


def _publish_many(publisher, entity_ids: list[str]) -> None:
    for entity_id in entity_ids:
        publisher.publish(entity_id)
