from __future__ import annotations

import asyncio
import logging

from celery import Task

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.schemas.jobs import ImageItemStatus
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.tagging import TaggingOutcome, get_tag_provider
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class ImageTaggingTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        image_id = args[0] if args else kwargs.get("image_id")
        if image_id:
            try:
                asyncio.run(_complete_failed_tagging(image_id, "AI 标签任务失败，图片已保留"))
            except Exception:
                logger.exception("Unable to complete failed tagging task")


@celery_app.task(
    bind=True,
    base=ImageTaggingTask,
    name="image.generate_tags",
    queue="tagging",
    max_retries=0,
)
def generate_image_tags(task, image_id: str) -> None:
    asyncio.run(_generate_image_tags(image_id))


async def _generate_image_tags(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        if item is None or item.status != ImageItemStatus.TAGGING.value or item.ai_tag is None:
            return

        settings = load_ai_model_settings(get_settings())
        source_key = item.ai_tag.source_object_key
        try:
            image_bytes = await get_storage_provider().download(source_key)
            outcome = await _tag_with_retries(
                get_tag_provider(settings), image_bytes, settings.ai_tagging_max_retries
            )
        except Exception:
            logger.exception("Unable to load image for AI tagging")
            outcome = TaggingOutcome(status="failed", error_message="标签图片读取失败")

        await repository.upsert_ai_tag(
            image_id=item.id,
            source_object_key=source_key,
            provider=settings.ai_tagging_provider,
            model_name=settings.ai_tagging_model,
            prompt_version=item.ai_tag.prompt_version,
            status=outcome.status,
            duration_ms=outcome.duration_ms,
            tag_json=outcome.payload.model_dump() if outcome.payload else None,
            raw_response_json=outcome.raw_response,
            error_message=outcome.error_message,
        )
        reason = "AI 内容标签已生成" if outcome.status == "completed" else "AI 标签暂不可用，图片已保留"
        await repository.complete_tagging(item, reason=reason)


async def _tag_with_retries(provider, image_bytes: bytes, max_retries: int) -> TaggingOutcome:
    outcome = await provider.tag(image_bytes)
    for _ in range(max_retries):
        if outcome.status == "completed" or outcome.error_message == "未配置 AI_TAGGING_API_KEY":
            return outcome
        outcome = await provider.tag(image_bytes)
    return outcome


async def _complete_failed_tagging(image_id: str, reason: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        if item is not None and item.status == ImageItemStatus.TAGGING.value:
            await repository.complete_tagging(item, reason=reason)
