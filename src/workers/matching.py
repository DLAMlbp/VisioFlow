from __future__ import annotations

import asyncio
import logging

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.repositories.library import LibraryRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.embedding import ImageEmbeddingError, OpenClipImageEmbedder
from src.services.images.similarity import (
    ScoredCandidate,
    apply_shadow_mode,
    combined_similarity_score,
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
    if not get_settings().library_image_only_matching_enabled:
        logger.error(
            "Library matching is disabled; embedding remains pending image_id=%s",
            image_id,
        )
        return
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
        if embedding is None:
            emit_metric(
                logger,
                "embedding_failures_total",
                labels={
                    "source": "job",
                    "job_id": item.job_id,
                    "image_id": item.id,
                    "embedding_version": settings.image_embedding_version,
                },
            )
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
    workflow_settings = get_settings()
    if not workflow_settings.library_image_only_matching_enabled:
        logger.error(
            "Library matching is disabled; match remains queued image_id=%s", image_id
        )
        return
    if not workflow_settings.library_only_tags_enabled:
        logger.error("Library-only tags are disabled; match remains queued image_id=%s", image_id)
        return
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
        scored: list[ScoredCandidate] = []
        if item.embedding_status != "completed" or item.embedding is None:
            decision = unmatched_decision("本地图片向量暂不可用")
        else:
            try:
                similar_assets = await library_repository.find_similar_assets(
                    list(item.embedding),
                    similarity_profile.similarity_candidate_limit,
                )
                query_content = (
                    item.ai_tag.tag_json
                    if item.analysis_status == "completed"
                    and item.ai_tag is not None
                    and item.ai_tag.status == "completed"
                    else None
                )
                for asset, similarity_score in similar_assets:
                    feature_score = feature_similarity(query_content, asset.analysis_json)
                    scored.append(
                        ScoredCandidate(
                            asset=asset,
                            tags=list(asset.group.tags),
                            similarity_score=similarity_score,
                            feature_score=feature_score,
                            final_score=combined_similarity_score(
                                similarity_score=similarity_score,
                                feature_score=feature_score,
                                settings=similarity_profile,
                            ),
                        )
                    )
                decision = apply_shadow_mode(
                    decide_similarity(candidates=scored, settings=similarity_profile),
                    enabled=settings.library_match_shadow_mode,
                )
            except Exception:
                logger.exception("Unable to match image against material library")
                decision = unmatched_decision("素材库匹配暂不可用")

        matched_candidate = next(
            (candidate for candidate in scored if candidate.asset.id == decision.matched_asset_id),
            None,
        )
        metric_labels = {
            "decision": decision.decision,
            "job_id": item.job_id,
            "image_id": item.id,
            "embedding_version": item.embedding_version,
            "matched_asset_id": decision.matched_asset_id,
            "matched_group_id": (
                matched_candidate.asset.group_id if matched_candidate is not None else None
            ),
            "tag_snapshot": "|".join(decision.tags),
        }
        emit_metric(
            logger,
            "library_match_total",
            labels=metric_labels,
        )
        if decision.similarity_score is not None:
            emit_metric(
                logger,
                "library_match_score",
                value=round(decision.similarity_score, 6),
                labels=metric_labels,
            )
        if decision.feature_score is not None:
            emit_metric(
                logger,
                "library_match_feature_score",
                value=round(decision.feature_score, 6),
                labels=metric_labels,
            )
        if decision.final_score is not None:
            emit_metric(
                logger,
                "library_match_final_score",
                value=round(decision.final_score, 6),
                labels=metric_labels,
            )
        if len(decision.candidates) >= 2:
            margin = float(decision.candidates[0]["final_score"]) - float(
                decision.candidates[1]["final_score"]
            )
            emit_metric(
                logger,
                "library_match_margin",
                value=round(margin, 6),
                labels=metric_labels,
            )

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
        tag_json = dict(item.ai_tag.tag_json or {}) if item.ai_tag is not None else {}
        tag_json.update(
            {
                "tags": decision.tags if decision.decision == "matched" else [],
                "categories": (
                    {"素材库标签": decision.tags}
                    if decision.decision == "matched"
                    else {}
                ),
                "candidate_tags": [],
                "confidence": decision.final_score,
                "risks": list(tag_json.get("risks") or []),
            }
        )
        emit_metric(
            logger,
            "library_tags_written_total",
            labels=metric_labels,
        )
        if item.ai_tag is not None:
            await repository.upsert_ai_tag(
                image_id=item.id,
                source_object_key=item.ai_tag.source_object_key,
                provider="library",
                model_name=(
                    f"{settings.image_embedding_version}+{settings.ai_tagging_model}"
                )[:120],
                prompt_version=job.similarity_profile_id,
                status="completed",
                duration_ms=item.ai_tag.duration_ms,
                tag_json=tag_json,
                raw_response_json=item.ai_tag.raw_response_json,
                error_message=(decision.message if decision.decision == "unmatched" else None),
            )
        await repository.complete_match_stage(item.id)
        await repository.complete_tagging(item, reason=decision.message)
