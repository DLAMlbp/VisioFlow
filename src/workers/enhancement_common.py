from __future__ import annotations

import asyncio
import logging

from celery import Task

from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository

logger = logging.getLogger(__name__)


class EnhancementStageTask(Task):
    stage_name = "enhancement"
    cursor_stage = "enhance"

    def on_retry(self, exc, task_id, args, kwargs, einfo) -> None:
        image_id = args[0] if args else kwargs.get("image_id")
        if image_id:
            try:
                asyncio.run(release_enhancement_claim(str(image_id), self.cursor_stage))
            except Exception:
                logger.exception("Unable to release enhancement stage for retry")

    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        image_id = args[0] if args else kwargs.get("image_id")
        if image_id:
            try:
                asyncio.run(mark_enhancement_failed(str(image_id), self.stage_name))
            except Exception:
                logger.exception("Unable to mark failed enhancement stage")


async def mark_enhancement_failed(image_id: str, stage_name: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.get_item(image_id)
        emit_metric(
            logger,
            "enhancement_stage_failures_total",
            labels={
                "stage": stage_name,
                "image_id": image_id,
                "job_id": item.job_id if item is not None else None,
            },
        )
        if item is not None and item.status in {"filtered", "enhancing", "enhanced"}:
            await repository.fail_item(item, f"图片处理阶段失败：{stage_name}")


async def release_enhancement_claim(image_id: str, cursor_stage: str) -> None:
    async with AsyncSessionLocal() as session:
        await ImageJobRepository(session).release_enhancement_stage(
            image_id, cursor_stage
        )
