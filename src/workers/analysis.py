from __future__ import annotations

import asyncio
import logging

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.tagging import (
    PROMPT_VERSION,
    TaggingOutcome,
    TagPayload,
    analyze_many_with_retries,
    get_tag_provider,
    matching_content_payload,
)
from src.services.jobs.dispatch import MatchTaskPublisher
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="image.analyze_content", queue="analysis", max_retries=0)
def analyze_image_content(image_id: str) -> None:
    """Recognize matching-only content features from a stable source image."""
    asyncio.run(_analyze_image_content(image_id))


async def _analyze_image_content(image_id: str) -> None:
    workflow_settings = get_settings()
    settings = load_ai_model_settings(workflow_settings)
    batch_limit = max(1, min(settings.ai_tagging_concurrency, 8))
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        items = await repository.claim_analysis_batch(image_id, limit=batch_limit)
        if not items:
            return
        job = await repository.get_config(items[0].job_id)
        if job is None or job.cancel_requested_at is not None:
            return

        storage = get_storage_provider()
        analyzable_items = []
        image_bytes_batch: list[bytes] = []
        outcomes: dict[str, TaggingOutcome] = {}
        source_keys: dict[str, str] = {}
        for item in items:
            source_key = item.thumbnail_object_key or item.object_key
            if not source_key:
                outcomes[item.id] = TaggingOutcome(
                    status="failed",
                    error_message="缺少稳定的预处理内容分析文件",
                )
                continue
            reusable = (
                await repository.find_reusable_content_analysis(
                    image_id=item.id,
                    sha256=item.sha256,
                    prompt_version=PROMPT_VERSION,
                    model_name=settings.ai_tagging_model,
                )
                if getattr(item, "sha256", None)
                and hasattr(repository, "find_reusable_content_analysis")
                else None
            )
            if reusable is not None:
                try:
                    reusable_payload = dict(reusable)
                    reusable_payload["confidence"] = reusable_payload.get(
                        "content_confidence", reusable_payload.get("confidence", 0.0)
                    )
                    outcomes[item.id] = TaggingOutcome(
                        status="completed",
                        payload=matching_content_payload(
                            TagPayload.model_validate(reusable_payload)
                        ),
                    )
                except (TypeError, ValueError):
                    pass
            if reusable is not None and item.id in outcomes:
                source_keys[item.id] = source_key
                continue
            try:
                image_bytes_batch.append(await storage.download(source_key))
                analyzable_items.append(item)
                source_keys[item.id] = source_key
            except Exception:
                logger.exception("Unable to load image for content analysis")
                outcomes[item.id] = TaggingOutcome(
                    status="failed", error_message="内容分析文件读取失败"
                )

        if analyzable_items:
            try:
                analyzed = await analyze_many_with_retries(
                    get_tag_provider(settings),
                    image_bytes_batch,
                    settings.ai_tagging_max_retries,
                )
                outcomes.update(
                    {
                        item.id: outcome
                        for item, outcome in zip(analyzable_items, analyzed, strict=True)
                    }
                )
            except Exception:
                logger.exception("Unable to analyze beautified image content")
                outcomes.update(
                    {
                        item.id: TaggingOutcome(
                            status="failed", error_message="大模型内容特征识别暂不可用"
                        )
                        for item in analyzable_items
                    }
                )

        for item in items:
            outcome = outcomes[item.id]
            content = (
                matching_content_payload(outcome.payload)
                if outcome.status == "completed" and outcome.payload is not None
                else None
            )
            succeeded = content is not None
            serialized_content = content.model_dump(mode="json") if content else None
            if serialized_content is not None:
                serialized_content["content_confidence"] = content.confidence
            await repository.upsert_ai_tag(
                image_id=item.id,
                source_object_key=(
                    source_keys.get(item.id)
                    or item.thumbnail_object_key
                    or item.object_key
                ),
                provider=settings.ai_tagging_provider,
                model_name=settings.ai_tagging_model,
                prompt_version=PROMPT_VERSION,
                status="completed" if succeeded else "failed",
                duration_ms=outcome.duration_ms,
                tag_json=serialized_content,
                raw_response_json=(
                    outcome.raw_response if settings.ai_tagging_store_raw_response else None
                ),
                error_message=None if succeeded else outcome.error_message,
            )
            if not succeeded:
                await repository.fail_item(
                    item,
                    outcome.error_message or "大模型内容特征识别失败",
                    node="content_analysis",
                    code="UPSTREAM_UNAVAILABLE",
                    duration_ms=outcome.duration_ms,
                )
                # This batch claim may contain multiple independent images.  A
                # storage/model failure for one image must not strand the other
                # claimed rows in analysis_status=processing.
                continue
            await repository.complete_analysis_stage(item.id, succeeded=succeeded)
            if await repository.claim_match_if_ready(item.id):
                MatchTaskPublisher().publish(item.id)
