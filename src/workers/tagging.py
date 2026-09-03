from __future__ import annotations

import asyncio
import logging

from celery import Task

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.repositories.library import LibraryRepository
from src.schemas.jobs import ImageItemStatus
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.embedding import ImageEmbeddingError, OpenClipImageEmbedder
from src.services.images.similarity import (
    ScoredCandidate,
    combined_similarity_score,
    decide_similarity,
    feature_similarity,
    unmatched_decision,
)
from src.services.images.tagging import TaggingOutcome, get_tag_provider
from src.services.profiles import ProfileLoader
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
        library_repository = LibraryRepository(session)
        item = await repository.get_item(image_id)
        if item is None or item.status != ImageItemStatus.TAGGING.value or item.ai_tag is None:
            return

        job = await repository.get(item.job_id)
        if job is None:
            return
        settings = load_ai_model_settings(get_settings())
        similarity_profile = ProfileLoader(settings).get_similarity_profile(
            job.similarity_profile_id
        )
        source_key = item.ai_tag.source_object_key
        try:
            image_bytes = await get_storage_provider().download(source_key)
            outcome = await _tag_with_retries(
                get_tag_provider(settings), image_bytes, settings.ai_tagging_max_retries
            )
        except Exception:
            logger.exception("Unable to load image for AI tagging")
            outcome = TaggingOutcome(status="failed", error_message="标签图片读取失败")

        tag_json = outcome.payload.model_dump() if outcome.payload else None
        match_decision = unmatched_decision(
            outcome.error_message or "未识别到相似的图片素材"
        )
        if outcome.status == "completed" and outcome.payload is not None:
            try:
                embedding = await OpenClipImageEmbedder(settings).embed(image_bytes)
                similar_assets = await library_repository.find_similar_assets(
                    embedding,
                    similarity_profile.similarity_candidate_limit,
                )
                scored: list[ScoredCandidate] = []
                for asset, similarity_score in similar_assets:
                    feature_score = feature_similarity(
                        outcome.payload.model_dump(), asset.analysis_json
                    )
                    final_score = combined_similarity_score(
                        similarity_score=similarity_score,
                        feature_score=feature_score,
                        settings=similarity_profile,
                    )
                    scored.append(
                        ScoredCandidate(
                            asset=asset,
                            tags=list(asset.group.tags),
                            similarity_score=similarity_score,
                            feature_score=feature_score,
                            final_score=final_score,
                        )
                    )
                match_decision = decide_similarity(candidates=scored, settings=similarity_profile)
            except ImageEmbeddingError as exc:
                match_decision = unmatched_decision(str(exc))
            except Exception:
                logger.exception("Unable to match image against material library")
                match_decision = unmatched_decision("素材库匹配暂不可用")

        await library_repository.upsert_match(
            image_id=item.id,
            values={
                "matched_asset_id": match_decision.matched_asset_id,
                "matched_tags_snapshot": match_decision.tags,
                "similarity_score": match_decision.similarity_score,
                "feature_score": match_decision.feature_score,
                "final_score": match_decision.final_score,
                "decision": match_decision.decision,
                "message": match_decision.message,
                "candidate_json": match_decision.candidates,
            },
        )
        if tag_json is not None:
            tag_json["tags"] = (
                match_decision.tags if match_decision.decision == "matched" else []
            )
            tag_json["categories"] = (
                {"素材库标签": match_decision.tags}
                if match_decision.decision == "matched"
                else {}
            )
            tag_json["candidate_tags"] = (
                match_decision.tags if match_decision.decision == "pending_review" else []
            )

        await repository.upsert_ai_tag(
            image_id=item.id,
            source_object_key=source_key,
            provider=settings.ai_tagging_provider,
            model_name=settings.ai_tagging_model,
            prompt_version=item.ai_tag.prompt_version,
            status=outcome.status,
            duration_ms=outcome.duration_ms,
            tag_json=tag_json,
            raw_response_json=outcome.raw_response,
            error_message=outcome.error_message,
        )
        if outcome.status != "completed" or outcome.payload is None:
            await repository.fail_item(
                item,
                outcome.error_message or "AI 内容分析失败",
                node="content_analysis",
                code="UPSTREAM_UNAVAILABLE",
                duration_ms=outcome.duration_ms,
            )
            return
        reason = (
            match_decision.message
            if outcome.status == "completed"
            else "AI 内容分析暂不可用，图片已保留"
        )
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
