from __future__ import annotations

import asyncio
import logging

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.repositories.library import LibraryRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.embedding import ImageEmbeddingError, OpenClipImageEmbedder
from src.services.images.similarity import (
    ScoredCandidate,
    decide_similarity,
    feature_similarity,
    unmatched_decision,
)
from src.services.jobs.dispatch import MatchTaskPublisher
from src.services.profiles import ProfileLoader
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="image.generate_embedding", queue="embedding", max_retries=1)
def generate_image_embedding(image_id: str) -> None:
    asyncio.run(_generate_image_embedding(image_id))


async def _generate_image_embedding(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.claim_embedding(image_id)
        if item is None:
            return
        job = await repository.get_config(item.job_id)
        if job is None or job.cancel_requested_at is not None:
            return
        settings = load_ai_model_settings(get_settings())
        embedding: list[float] | None = None
        try:
            if not item.analysis_object_key:
                raise ImageEmbeddingError("缺少向量分析图片")
            image_bytes = await get_storage_provider().download(item.analysis_object_key)
            embedding = await OpenClipImageEmbedder(settings).embed(image_bytes)
        except Exception:
            logger.exception("Unable to generate image embedding")
        await repository.complete_embedding_stage(
            item.id,
            embedding=embedding,
            embedding_version=settings.image_embedding_version if embedding else None,
        )
        if await repository.claim_match_if_ready(item.id):
            MatchTaskPublisher().publish(item.id)


@celery_app.task(name="image.match_library", queue="embedding", max_retries=1)
def match_image_library(image_id: str) -> None:
    asyncio.run(_match_image_library(image_id))


async def _match_image_library(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        library_repository = LibraryRepository(session)
        item = await repository.claim_match(image_id)
        if item is None:
            return
        job = await repository.get_config(item.job_id)
        if job is None or job.cancel_requested_at is not None:
            return
        settings = load_ai_model_settings(get_settings())
        similarity_profile = ProfileLoader(settings).get_similarity_profile(
            job.similarity_profile_id
        )
        tag_json = dict(item.ai_tag.tag_json or {}) if item.ai_tag else {}
        if item.analysis_status != "completed":
            decision = unmatched_decision(
                item.ai_tag.error_message if item.ai_tag and item.ai_tag.error_message else "AI 内容分析暂不可用"
            )
        elif item.embedding_status != "completed" or item.embedding is None:
            decision = unmatched_decision("本地图片向量暂不可用")
        else:
            try:
                similar_assets = await library_repository.find_similar_assets(
                    list(item.embedding),
                    similarity_profile.similarity_candidate_limit,
                )
                scored: list[ScoredCandidate] = []
                for asset, similarity_score in similar_assets:
                    feature_score = feature_similarity(tag_json, asset.analysis_json)
                    final_score = (
                        similarity_score * similarity_profile.similarity_image_weight
                        + feature_score * similarity_profile.similarity_feature_weight
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
                decision = decide_similarity(candidates=scored, settings=similarity_profile)
            except Exception:
                logger.exception("Unable to match image against material library")
                decision = unmatched_decision("素材库匹配暂不可用")

        await library_repository.upsert_match(
            image_id=item.id,
            values={
                "matched_asset_id": decision.matched_asset_id,
                "matched_tags_snapshot": decision.tags,
                "similarity_score": decision.similarity_score,
                "feature_score": decision.feature_score,
                "final_score": decision.final_score,
                "decision": decision.decision,
                "message": decision.message,
                "candidate_json": decision.candidates,
            },
        )
        recognized_tags = [str(value) for value in (tag_json.get("tags") or [])]
        recognized_categories = dict(tag_json.get("categories") or {})
        recognized_candidates = [
            str(value) for value in (tag_json.get("candidate_tags") or [])
        ]
        if decision.decision == "matched":
            tag_json["tags"] = list(
                dict.fromkeys([*decision.tags, *recognized_tags])
            )[:8]
            tag_json["categories"] = {
                **recognized_categories,
                "素材库标签": decision.tags,
            }
        else:
            tag_json["tags"] = recognized_tags[:8]
            tag_json["categories"] = recognized_categories
        tag_json["candidate_tags"] = (
            list(dict.fromkeys([*decision.tags, *recognized_candidates]))[:8]
            if decision.decision == "pending_review"
            else recognized_candidates[:8]
        )
        if item.ai_tag is not None:
            await repository.upsert_ai_tag(
                image_id=item.id,
                source_object_key=item.ai_tag.source_object_key,
                provider=item.ai_tag.provider,
                model_name=item.ai_tag.model_name,
                prompt_version=item.ai_tag.prompt_version,
                status=item.ai_tag.status,
                duration_ms=item.ai_tag.duration_ms,
                tag_json=tag_json,
                raw_response_json=item.ai_tag.raw_response_json,
                error_message=item.ai_tag.error_message,
            )
        await repository.complete_match_stage(item.id)
        await repository.complete_tagging(item, reason=decision.message)
