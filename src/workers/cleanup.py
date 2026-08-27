from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.models.image_item import ImageItem
from src.models.image_job import ImageJob
from src.models.upload_batch import UploadBatch, UploadBatchItem
from src.services.jobs.dispatch import (
    AnalysisTaskPublisher,
    EmbeddingTaskPublisher,
    EnhancementTaskPublisher,
    JobDispatchTaskPublisher,
    MatchTaskPublisher,
    MetadataTaskPublisher,
)
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="maintenance.cleanup_expired_images", queue="cleanup", max_retries=2)
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
            keys = [item.object_key, item.thumbnail_object_key, item.analysis_object_key]
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
                    logger.debug("Unable to delete orphan upload %s", item.object_key, exc_info=True)
                item.status = "expired"
            if batch.status == "registered":
                batch.status = "expired"
        await session.commit()


@celery_app.task(name="maintenance.recover_stalled_images", queue="cleanup", max_retries=2)
def recover_stalled_images() -> None:
    asyncio.run(_recover_stalled_images())


async def _recover_stalled_images() -> None:
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
        preprocessing = await _reset_stage(
            session,
            status="analyzing",
            timestamp=ImageItem.preprocess_started_at,
            cutoff=cutoff,
            values={"status": "queued", "preprocess_started_at": None},
        )
        enhancing = await _reset_stage(
            session,
            status="enhancing",
            timestamp=ImageItem.enhance_started_at,
            cutoff=cutoff,
            values={"status": "filtered", "enhance_started_at": None},
        )
        analyzing = await _reset_substage(
            session,
            column=ImageItem.analysis_status,
            timestamp=ImageItem.analysis_started_at,
            cutoff=cutoff,
        )
        embedding = await _reset_substage(
            session,
            column=ImageItem.embedding_status,
            timestamp=ImageItem.embedding_started_at,
            cutoff=cutoff,
        )
        matching = await _reset_substage(
            session,
            column=ImageItem.match_status,
            timestamp=ImageItem.match_started_at,
            cutoff=cutoff,
            reset_to="queued",
        )
        await session.commit()

    _publish_many(JobDispatchTaskPublisher(), undispatched_jobs)
    _publish_many(MetadataTaskPublisher(), [*queued, *preprocessing])
    _publish_many(EnhancementTaskPublisher(), enhancing)
    _publish_many(AnalysisTaskPublisher(), analyzing)
    _publish_many(EmbeddingTaskPublisher(), embedding)
    _publish_many(MatchTaskPublisher(), matching)


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
    session, *, column, timestamp, cutoff, reset_to: str = "pending"
) -> list[str]:
    return list(
        (
            await session.execute(
                update(ImageItem)
                .where(ImageItem.status == "tagging", column == "processing", timestamp < cutoff)
                .values({column.key: reset_to, timestamp.key: None})
                .returning(ImageItem.id)
            )
        ).scalars()
    )


def _publish_many(publisher, entity_ids: list[str]) -> None:
    for entity_id in entity_ids:
        publisher.publish(entity_id)
