from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.jobs.callbacks import (
    build_job_callback_payload,
    callback_destination,
    post_job_callback,
)
from src.services.jobs.dispatch import CallbackTaskPublisher
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="image.deliver_callback", queue="callback", max_retries=0)
def deliver_job_callback(job_id: str) -> None:
    asyncio.run(_deliver_job_callback(job_id))


async def _deliver_job_callback(job_id: str) -> None:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        callback_job = await repository.claim_callback_delivery(
            job_id,
            lease_seconds=settings.callback_delivery_lease_seconds,
        )
        if callback_job is None:
            return

        try:
            payload = await build_job_callback_payload(
                callback_job,
                repository,
                settings,
                get_storage_provider(),
            )
            await post_job_callback(
                callback_job.callback_url,
                payload,
                timeout_seconds=settings.callback_timeout_seconds,
                signing_secret=settings.callback_signing_secret,
                allowed_hosts=(
                    ""
                    if callback_job.callback_contract == "customer_v1"
                    else settings.callback_allowed_hosts
                ),
            )
        except Exception as exc:  # noqa: BLE001 - persist and retry every delivery failure
            retry_at = None
            if callback_job.attempts < settings.callback_max_attempts:
                delay = settings.callback_retry_base_seconds * (2 ** (callback_job.attempts - 1))
                retry_at = datetime.now(UTC) + timedelta(seconds=min(delay, 3600))
            await repository.mark_callback_failed(
                callback_job.id,
                error=str(exc),
                retry_at=retry_at,
            )
            logger.warning(
                "Callback delivery failed job_id=%s destination=%s attempt=%s retry=%s error=%s",
                callback_job.id,
                callback_destination(callback_job.callback_url),
                callback_job.attempts,
                retry_at is not None,
                str(exc)[:500],
            )
            return

        await repository.mark_callback_delivered(callback_job.id)
        logger.info(
            "Callback delivered job_id=%s destination=%s attempt=%s",
            callback_job.id,
            callback_destination(callback_job.callback_url),
            callback_job.attempts,
        )


@celery_app.task(name="maintenance.recover_pending_callbacks", queue="callback", max_retries=0)
def recover_pending_callbacks() -> None:
    asyncio.run(_recover_pending_callbacks())


async def _recover_pending_callbacks() -> None:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        job_ids = await repository.list_callback_jobs_due(
            limit=settings.callback_recovery_batch_size,
            lease_seconds=settings.callback_delivery_lease_seconds,
        )
    publisher = CallbackTaskPublisher()
    for job_id in job_ids:
        publisher.publish(job_id)
