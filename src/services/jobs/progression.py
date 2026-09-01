import logging
from datetime import UTC, datetime

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.repositories.jobs import ImageJobRepository
from src.schemas.jobs import ImageItemStatus
from src.services.jobs.dispatch import (
    AnalysisTaskPublisher,
    BeautifyPlanTaskPublisher,
    ProvisionalEmbeddingTaskPublisher,
    RankingTaskPublisher,
)

logger = logging.getLogger(__name__)


async def advance_after_preprocess(repository: ImageJobRepository, item) -> None:
    """Publish the next durable stage after filtering without importing a worker module."""
    job = await repository.get_config(item.job_id)
    if job is None or job.cancel_requested_at is not None:
        return
    if getattr(job, "routing_mode", "legacy") == "streaming_v2":
        if item.status != ImageItemStatus.FILTERED.value:
            return
        if get_settings().early_semantic_branch_enabled:
            queued = await repository.queue_post_filter_branches(
                item.id,
                similarity_enabled=job.similarity_enabled,
            )
            if queued:
                BeautifyPlanTaskPublisher().publish(item.id)
                if job.similarity_enabled:
                    AnalysisTaskPublisher().publish(item.id)
                    ProvisionalEmbeddingTaskPublisher().publish(item.id)
        elif await repository.queue_beautify_plan(item.id):
            BeautifyPlanTaskPublisher().publish(item.id)
        return
    if not get_settings().batch_filter_barrier_enabled:
        return
    if await repository.claim_ranking_if_filtering_complete(job.id):
        waited = max(
            0.0,
            (
                datetime.now(UTC)
                - getattr(item, "created_at", datetime.now(UTC))
            ).total_seconds(),
        )
        emit_metric(
            logger,
            "filter_barrier_wait_seconds",
            value=round(waited, 3),
            labels={"job_id": job.id},
        )
        emit_metric(
            logger,
            "filter_barrier_trigger_total",
            labels={"job_id": job.id},
        )
        RankingTaskPublisher().publish(job.id)
