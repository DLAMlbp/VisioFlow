from __future__ import annotations

import asyncio

from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.images.processing_vision import content_from_processing_json
from src.services.jobs.dispatch import MatchTaskPublisher
from src.workers.celery_app import celery_app


@celery_app.task(name="image.analyze_content", queue="analysis", max_retries=0)
def analyze_image_content(image_id: str) -> None:
    """Attach the original recognition result to the enhanced image.

    The original-image processing call already performs semantic recognition.
    This compatibility stage deliberately does not invoke a vision model again.
    """
    asyncio.run(_analyze_image_content(image_id))


async def _analyze_image_content(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        items = await repository.claim_analysis_batch(image_id, limit=50)
        if not items:
            return
        job = await repository.get_config(items[0].job_id)
        if job is None or job.cancel_requested_at is not None:
            return

        for item in items:
            content = content_from_processing_json(item.ai_processing_json)
            source_key = item.analysis_object_key or ""
            succeeded = content is not None and bool(source_key)
            existing_tag = item.ai_tag
            await repository.upsert_ai_tag(
                image_id=item.id,
                source_object_key=source_key,
                provider=(existing_tag.provider if existing_tag else "openai"),
                model_name=(
                    item.ai_processing_model
                    or (existing_tag.model_name if existing_tag else "unknown")
                ),
                prompt_version=(
                    item.ai_processing_prompt_version
                    or (existing_tag.prompt_version if existing_tag else "single_recognition")
                ),
                status="completed" if succeeded else "failed",
                duration_ms=item.ai_processing_duration_ms,
                tag_json=content.model_dump(mode="json") if content else None,
                raw_response_json=None,
                error_message=(
                    None
                    if succeeded
                    else "首次 AI 识别结果缺少可复用的标签信息"
                    if content is None
                    else "缺少美化图分析文件"
                ),
            )
            await repository.complete_analysis_stage(item.id, succeeded=succeeded)
            if await repository.claim_match_if_ready(item.id):
                MatchTaskPublisher().publish(item.id)
