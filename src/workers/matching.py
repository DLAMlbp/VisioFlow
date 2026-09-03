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
    SCORE_VERSION,
    ScoredCandidate,
    decide_similarity,
    score_candidate,
)
from src.services.jobs.dispatch import EmbeddingTaskPublisher, MatchTaskPublisher
from src.services.profiles import ProfileLoader
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="image.generate_embedding",
    queue="openclip",
    priority=9,
    max_retries=0,
)
def generate_image_embedding(image_id: str) -> None:
    asyncio.run(_generate_image_embedding(image_id))


async def _generate_image_embedding(image_id: str) -> None:
    workflow_settings = get_settings()
    early_semantic = getattr(workflow_settings, "early_semantic_branch_enabled", False)
    if not workflow_settings.library_image_only_matching_enabled:
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
        settings = load_ai_model_settings(workflow_settings)
        embedding_version = f"{settings.image_embedding_version}+source_v2"[:120]
        embedding: list[float] | None = None
        provisional = bool(
            early_semantic
            and not (item.status == "enhanced" and item.analysis_object_key)
        )
        try:
            embedding = (
                await repository.find_reusable_image_embedding(
                    image_id=item.id,
                    sha256=item.sha256,
                    embedding_version=embedding_version,
                )
                if getattr(item, "sha256", None)
                and hasattr(repository, "find_reusable_image_embedding")
                else None
            )
            source_key = item.thumbnail_object_key or getattr(item, "object_key", None)
            if embedding is None and not source_key:
                raise ImageEmbeddingError("缺少向量分析图片")
            if embedding is None:
                image_bytes = await get_storage_provider().download(source_key)
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
                    "embedding_version": embedding_version,
                },
            )
            await repository.fail_item(
                item,
                "图片向量生成失败",
                node="embedding",
                code="EMBEDDING_FAILED",
            )
            return
        await repository.complete_embedding_stage(
            item.id,
            embedding=embedding,
            embedding_version=embedding_version if embedding else None,
            provisional=provisional,
        )
        emit_metric(
            logger,
            "embedding_generation_total",
            labels={
                "phase": "provisional" if provisional else "final",
                "outcome": "success" if embedding is not None else "failed",
                "job_id": item.job_id,
                "image_id": item.id,
                "embedding_version": embedding_version,
            },
        )
        if provisional:
            refreshed = await repository.get_item(item.id)
            if (
                refreshed is not None
                and refreshed.status == "enhanced"
                and await repository.queue_final_embedding(item.id)
            ):
                EmbeddingTaskPublisher().publish(item.id)
            return
        if await repository.claim_match_if_ready(item.id):
            MatchTaskPublisher().publish(item.id)


@celery_app.task(name="image.match_library", queue="matching", max_retries=0)
def match_image_library(image_id: str) -> None:
    asyncio.run(_match_image_library(image_id))


async def _match_image_library(image_id: str) -> None:
    workflow_settings = get_settings()
    early_semantic = getattr(workflow_settings, "early_semantic_branch_enabled", False)
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
            await repository.fail_item(
                item,
                "素材匹配缺少有效图片向量",
                node="matching",
                code="EMBEDDING_MISSING",
            )
            return
        else:
            try:
                exact_assets = (
                    await library_repository.find_exact_active_assets(item.sha256)
                    if getattr(item, "sha256", None)
                    and hasattr(library_repository, "find_exact_active_assets")
                    else []
                )
                query_content = (
                    item.ai_tag.tag_json
                    if item.analysis_status == "completed"
                    and item.ai_tag is not None
                    and item.ai_tag.status == "completed"
                    else None
                )
                if exact_assets:
                    scored.extend(
                        ScoredCandidate(
                            asset=asset,
                            tags=list(asset.group.tags),
                            similarity_score=1.0,
                            feature_score=1.0,
                            final_score=1.0,
                            feature_reliability=1.0,
                            feature_coverage=1.0,
                            score_version=SCORE_VERSION,
                        )
                        for asset in exact_assets
                    )
                else:
                    similar_assets = await library_repository.find_similar_assets(
                        list(item.embedding),
                        similarity_profile.similarity_candidate_limit,
                    )
                    for asset, similarity_score in similar_assets:
                        scored.append(
                            score_candidate(
                                asset=asset,
                                tags=list(asset.group.tags),
                                similarity_score=similarity_score,
                                query_content=query_content,
                                settings=similarity_profile,
                            )
                        )
                decision = decide_similarity(
                    candidates=scored, settings=similarity_profile
                )
            except Exception as exc:
                logger.exception("Unable to match image against material library")
                await repository.fail_item(
                    item,
                    f"素材库匹配失败：{exc}",
                    node="matching",
                    code="MATCHING_FAILED",
                )
                return

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
        if decision.feature_reliability is not None:
            emit_metric(
                logger,
                "library_match_feature_reliability",
                value=round(decision.feature_reliability, 6),
                labels=metric_labels,
            )
        if decision.feature_coverage is not None:
            emit_metric(
                logger,
                "library_match_feature_coverage",
                value=round(decision.feature_coverage, 6),
                labels=metric_labels,
            )
        if decision.final_score is not None:
            emit_metric(
                logger,
                "library_match_final_score",
                value=round(decision.final_score, 6),
                labels=metric_labels,
            )
        if decision.candidate_margin is not None:
            emit_metric(
                logger,
                "library_match_margin",
                value=round(decision.candidate_margin, 6),
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
                "score_version": decision.score_version,
                "feature_reliability": decision.feature_reliability,
                "feature_coverage": decision.feature_coverage,
                "candidate_margin": decision.candidate_margin,
                "field_scores": decision.field_scores,
                "decision": decision.decision,
                "message": decision.message,
                "candidate_json": decision.candidates,
            },
        )
        tag_json = dict(item.ai_tag.tag_json or {}) if item.ai_tag is not None else {}
        if item.ai_tag is not None:
            tag_json["content_analysis_provenance"] = {
                "provider": item.ai_tag.provider,
                "model_name": item.ai_tag.model_name,
                "prompt_version": item.ai_tag.prompt_version,
            }
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
                "match_confidence": decision.final_score,
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
                model_name=item.embedding_version or settings.image_embedding_version,
                prompt_version=job.similarity_profile_id,
                status="completed",
                duration_ms=item.ai_tag.duration_ms,
                tag_json=tag_json,
                raw_response_json=item.ai_tag.raw_response_json,
                error_message=(decision.message if decision.decision == "unmatched" else None),
            )
        await repository.complete_match_stage(item.id)
        if early_semantic:
            await repository.finalize_selected_if_ready(item, reason=decision.message)
        else:
            await repository.complete_tagging(item, reason=decision.message)
