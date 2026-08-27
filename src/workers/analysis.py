from __future__ import annotations

import asyncio
import random
import time

from redis.asyncio import Redis

from src.core.config import Settings, get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.tagging import TaggingOutcome, get_tag_provider
from src.services.jobs.dispatch import MatchTaskPublisher
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app


@celery_app.task(name="image.analyze_content", queue="analysis", max_retries=0)
def analyze_image_content(image_id: str) -> None:
    asyncio.run(_analyze_image_content(image_id))


async def _analyze_image_content(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.claim_analysis(image_id)
        if item is None:
            return
        job = await repository.get_config(item.job_id)
        if job is None or job.cancel_requested_at is not None:
            return
        settings = load_ai_model_settings(get_settings())
        source_key = item.analysis_object_key
        if not source_key:
            outcome = TaggingOutcome(status="failed", error_message="缺少模型分析图片")
        else:
            try:
                image_bytes = await get_storage_provider().download(source_key)
                await _acquire_rate_slot(settings)
                outcome = await _tag_with_backoff(
                    get_tag_provider(settings), image_bytes, settings.ai_tagging_max_retries
                )
            except Exception:  # noqa: BLE001 - provider/storage errors become per-image failures
                outcome = TaggingOutcome(status="failed", error_message="标签图片读取失败")
        await repository.upsert_ai_tag(
            image_id=item.id,
            source_object_key=source_key or "",
            provider=settings.ai_tagging_provider,
            model_name=settings.ai_tagging_model,
            prompt_version=item.ai_tag.prompt_version if item.ai_tag else "renovation_auto_generic_v2",
            status=outcome.status,
            duration_ms=outcome.duration_ms,
            tag_json=outcome.payload.model_dump() if outcome.payload else None,
            raw_response_json=(
                outcome.raw_response if settings.ai_tagging_store_raw_response else None
            ),
            error_message=outcome.error_message,
        )
        await repository.complete_analysis_stage(
            item.id, succeeded=outcome.status == "completed"
        )
        if await repository.claim_match_if_ready(item.id):
            MatchTaskPublisher().publish(item.id)


async def _tag_with_backoff(provider, image_bytes: bytes, max_retries: int) -> TaggingOutcome:
    outcome = await provider.tag(image_bytes)
    for attempt in range(max_retries):
        if outcome.status == "completed" or not outcome.retryable:
            return outcome
        await asyncio.sleep((2**attempt) + random.uniform(0.2, 0.8))
        outcome = await provider.tag(image_bytes)
    return outcome


async def _acquire_rate_slot(settings: Settings) -> None:
    limit = max(1, settings.ai_tagging_rate_limit_per_minute)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        while True:
            window = int(time.time() // 60)
            key = f"image-ai:luna-rate:{window}"
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, 65)
            if count <= limit:
                return
            ttl = await redis.ttl(key)
            await asyncio.sleep(max(1, min(10, ttl if ttl > 0 else 1)))
    finally:
        await redis.aclose()
