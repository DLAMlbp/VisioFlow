from __future__ import annotations

import asyncio
import logging
import random

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.processing_vision import content_from_processing_json
from src.services.images.tagging import TaggingOutcome, get_tag_provider
from src.services.images.vision_rate_limit import acquire_vision_rate_slot
from src.services.jobs.dispatch import MatchTaskPublisher
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="image.analyze_content", queue="analysis", max_retries=0)
def analyze_image_content(image_id: str) -> None:
    asyncio.run(_analyze_image_content(image_id))


async def _analyze_image_content(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        settings = load_ai_model_settings(get_settings())
        items = await repository.claim_analysis_batch(
            image_id, limit=max(1, settings.ai_tagging_concurrency)
        )
        if not items:
            return
        job = await repository.get_config(items[0].job_id)
        if job is None or job.cancel_requested_at is not None:
            return
        outcomes: list[TaggingOutcome | None] = [None] * len(items)
        source_keys = [item.analysis_object_key or "" for item in items]
        fallback_indexes: list[int] = []
        for index, item in enumerate(items):
            reused_content = (
                content_from_processing_json(item.ai_processing_json)
                if item.ai_processing_status == "completed"
                else None
            )
            if reused_content is None:
                fallback_indexes.append(index)
                continue
            outcomes[index] = TaggingOutcome(
                status="completed",
                payload=reused_content,
                duration_ms=0,
            )
            source_keys[index] = item.object_key

        storage = get_storage_provider()
        downloads = await asyncio.gather(
            *(
                storage.download(items[index].analysis_object_key)
                if items[index].analysis_object_key
                else _missing_analysis_image()
                for index in fallback_indexes
            ),
            return_exceptions=True,
        )
        ready_indexes = [
            fallback_indexes[position]
            for position, image_bytes in enumerate(downloads)
            if isinstance(image_bytes, bytes)
        ]
        if ready_indexes:
            try:
                await acquire_vision_rate_slot(settings)
                ready_outcomes = await _tag_many_with_backoff(
                    get_tag_provider(settings),
                    [
                        downloads[fallback_indexes.index(index)]
                        for index in ready_indexes
                    ],
                    settings.ai_tagging_max_retries,
                )
                for index, outcome in zip(ready_indexes, ready_outcomes, strict=True):
                    outcomes[index] = outcome
            except Exception:
                logger.exception("Unable to analyze fallback image batch")

        for item_index, (item, outcome) in enumerate(zip(items, outcomes, strict=True)):
            source_key = source_keys[item_index]
            if outcome is None:
                outcome = TaggingOutcome(
                    status="failed",
                    error_message=(
                        "缺少模型分析图片" if not source_key else "标签图片读取失败"
                    ),
                )
            reused = source_key == item.object_key and outcome.payload is not None
            model_name = (
                item.ai_processing_model or settings.ai_tagging_model
                if reused
                else settings.ai_tagging_model
            )
            prompt_version = (
                item.ai_processing_prompt_version or "managed_filter_beautify_content_v1"
                if reused
                else item.ai_tag.prompt_version
                if item.ai_tag
                else "renovation_auto_generic_v2"
            )
            await repository.upsert_ai_tag(
                image_id=item.id,
                source_object_key=source_key,
                provider=settings.ai_tagging_provider,
                model_name=model_name,
                prompt_version=prompt_version,
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


async def _tag_many_with_backoff(
    provider, images: list[bytes], max_retries: int
) -> list[TaggingOutcome]:
    outcomes = await provider.tag_many(images)
    for attempt in range(max_retries):
        if all(outcome.status == "completed" for outcome in outcomes) or not any(
            outcome.retryable for outcome in outcomes
        ):
            return outcomes
        await asyncio.sleep((2**attempt) + random.uniform(0.2, 0.8))
        outcomes = await provider.tag_many(images)
    return outcomes


async def _missing_analysis_image() -> bytes:
    raise ValueError("缺少模型分析图片")
