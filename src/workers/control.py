import asyncio

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.jobs.dispatch import BeautifyPlanTaskPublisher, MetadataTaskPublisher
from src.workers.celery_app import celery_app


@celery_app.task(name="image.dispatch_job", queue="control", max_retries=0)
def dispatch_job(job_id: str) -> None:
    asyncio.run(_dispatch_job(job_id))


async def _dispatch_job(job_id: str) -> None:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        job = await repository.get_config(job_id)
        if job is None or job.cancel_requested_at is not None:
            return
        image_ids = await repository.list_item_ids_for_dispatch(
            job_id,
            offset=job.dispatch_cursor,
            limit=settings.job_dispatch_chunk_size,
        )
        if not image_ids:
            return
        publisher = MetadataTaskPublisher()
        for image_id in image_ids:
            publisher.publish(image_id)
        next_cursor = job.dispatch_cursor + len(image_ids)
        await repository.mark_preprocess_dispatched(image_ids)
        await repository.advance_dispatch_cursor(job_id, next_cursor)
        if next_cursor < job.total_count:
            celery_app.send_task(
                "image.dispatch_job", args=[job_id], queue="control", countdown=1
            )


@celery_app.task(name="image.rank_job", queue="control", max_retries=0)
def rank_job(job_id: str) -> None:
    asyncio.run(_rank_job(job_id))


async def _rank_job(job_id: str) -> None:
    settings = get_settings()
    if not settings.batch_filter_barrier_enabled:
        return
    if not settings.post_filter_beautify_plan_enabled:
        return
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        job = await repository.get_config(job_id)
        if job is None or job.cancel_requested_at is not None:
            return
        if getattr(job, "routing_mode", "legacy") == "streaming_v2":
            return
        filtered = await repository.list_filtered_items_by_score(job_id)
        selected = filtered[: job.max_selected]
        not_selected = filtered[job.max_selected :]
        for item in not_selected:
            await repository.mark_not_selected(item)
        await repository.finish_ranking(job_id)
        publisher = BeautifyPlanTaskPublisher()
        for item in selected:
            if await repository.queue_beautify_plan(item.id):
                publisher.publish(item.id)
        await repository.complete_job_if_finished(job_id)
