from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import cast, delete, desc, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import BIT
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.image_ai_tag import ImageAITag
from src.models.image_item import ImageItem
from src.models.image_job import ImageJob
from src.models.image_metric import ImageMetric
from src.models.image_result import ImageResult
from src.models.image_similarity_match import ImageSimilarityMatch


@dataclass(frozen=True)
class JobConfig:
    id: str
    status: str
    filter_profile_id: str
    beautify_profile_id: str
    filter_profile_snapshot: dict[str, object] | None
    beautify_profile_snapshot: dict[str, object] | None
    similarity_profile_id: str
    max_selected: int
    total_count: int
    dispatch_cursor: int
    cancel_requested_at: datetime | None


@dataclass(frozen=True)
class JobProgressSnapshot:
    id: str
    status: str
    total_count: int
    processed_count: int
    selected_count: int
    rejected_count: int
    not_selected_count: int
    stage_counts: dict[str, int]


@dataclass(frozen=True)
class CallbackJob:
    id: str
    callback_url: str
    status: str
    completed_at: datetime
    attempts: int


class ImageJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, job: ImageJob, items: list[ImageItem]) -> ImageJob:
        self.session.add(job)
        self.session.add_all(items)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def get(self, job_id: str) -> ImageJob | None:
        result = await self.session.execute(
            select(ImageJob)
            .where(ImageJob.id == job_id)
            .options(
                selectinload(ImageJob.items).selectinload(ImageItem.metric),
                selectinload(ImageJob.items).selectinload(ImageItem.result),
                selectinload(ImageJob.items).selectinload(ImageItem.ai_tag),
            )
        )
        return result.scalar_one_or_none()

    async def get_config(self, job_id: str) -> JobConfig | None:
        row = (
            await self.session.execute(
                select(
                    ImageJob.id,
                    ImageJob.status,
                    ImageJob.filter_profile_id,
                    ImageJob.beautify_profile_id,
                    ImageJob.filter_profile_snapshot,
                    ImageJob.beautify_profile_snapshot,
                    ImageJob.similarity_profile_id,
                    ImageJob.max_selected,
                    ImageJob.total_count,
                    ImageJob.dispatch_cursor,
                    ImageJob.cancel_requested_at,
                ).where(ImageJob.id == job_id)
            )
        ).one_or_none()
        return JobConfig(*row) if row else None

    async def list_item_ids_for_dispatch(
        self, job_id: str, *, offset: int, limit: int
    ) -> list[str]:
        result = await self.session.execute(
            select(ImageItem.id)
            .where(ImageItem.job_id == job_id)
            .order_by(ImageItem.created_at, ImageItem.id)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars())

    async def advance_dispatch_cursor(self, job_id: str, cursor: int) -> None:
        await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == job_id, ImageJob.dispatch_cursor < cursor)
            .values(dispatch_cursor=cursor)
        )
        await self.session.commit()

    async def mark_preprocess_dispatched(self, image_ids: list[str]) -> None:
        if not image_ids:
            return
        await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id.in_(image_ids), ImageItem.preprocess_dispatched_at.is_(None))
            .values(preprocess_dispatched_at=func.now())
        )
        await self.session.commit()

    async def get_item(self, image_id: str) -> ImageItem | None:
        result = await self.session.execute(
            select(ImageItem)
            .where(ImageItem.id == image_id)
            .options(
                selectinload(ImageItem.metric),
                selectinload(ImageItem.result),
                selectinload(ImageItem.ai_tag),
            )
        )
        return result.scalar_one_or_none()

    async def claim_preprocess(self, image_id: str) -> ImageItem | None:
        row = await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == image_id, ImageItem.status == "queued")
            .values(status="analyzing", preprocess_started_at=func.now())
            .returning(ImageItem.job_id)
        )
        job_id = row.scalar_one_or_none()
        if job_id is None:
            await self.session.rollback()
            return None
        await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == job_id, ImageJob.cancel_requested_at.is_(None))
            .values(status="processing", started_at=func.coalesce(ImageJob.started_at, func.now()))
        )
        await self.session.commit()
        return await self.get_item(image_id)

    async def claim_enhancement(self, image_id: str) -> ImageItem | None:
        row = await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == image_id, ImageItem.status == "filtered")
            .values(status="enhancing", enhance_started_at=func.now())
            .returning(ImageItem.id)
        )
        if row.scalar_one_or_none() is None:
            await self.session.rollback()
            return None
        await self.session.commit()
        return await self.get_item(image_id)

    async def claim_analysis(self, image_id: str) -> ImageItem | None:
        result = await self.session.execute(
            update(ImageItem)
            .where(
                ImageItem.id == image_id,
                ImageItem.status == "tagging",
                ImageItem.analysis_status == "pending",
            )
            .values(analysis_status="processing", analysis_started_at=func.now())
            .returning(ImageItem.id)
        )
        if result.scalar_one_or_none() is None:
            await self.session.rollback()
            return None
        await self.session.commit()
        return await self.get_item(image_id)

    async def claim_analysis_batch(self, image_id: str, *, limit: int) -> list[ImageItem]:
        job_id = await self.session.scalar(
            select(ImageItem.job_id).where(ImageItem.id == image_id)
        )
        if job_id is None:
            await self.session.rollback()
            return []
        candidates = (
            select(ImageItem.id)
            .where(
                ImageItem.job_id == job_id,
                ImageItem.status == "tagging",
                ImageItem.analysis_status == "pending",
            )
            .order_by(ImageItem.created_at, ImageItem.id)
            .limit(max(1, limit))
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id.in_(candidates))
            .values(analysis_status="processing", analysis_started_at=func.now())
            .returning(ImageItem.id)
        )
        claimed_ids = list(result.scalars())
        await self.session.commit()
        return [item for item_id in claimed_ids if (item := await self.get_item(item_id))]

    async def complete_analysis_stage(self, image_id: str, *, succeeded: bool) -> None:
        await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == image_id, ImageItem.analysis_status == "processing")
            .values(
                analysis_status="completed" if succeeded else "failed",
                analysis_completed_at=func.now(),
            )
        )
        await self.session.commit()

    async def claim_embedding(self, image_id: str) -> ImageItem | None:
        result = await self.session.execute(
            update(ImageItem)
            .where(
                ImageItem.id == image_id,
                ImageItem.status == "tagging",
                ImageItem.embedding_status == "pending",
            )
            .values(embedding_status="processing", embedding_started_at=func.now())
            .returning(ImageItem.id)
        )
        if result.scalar_one_or_none() is None:
            await self.session.rollback()
            return None
        await self.session.commit()
        return await self.get_item(image_id)

    async def complete_embedding_stage(
        self,
        image_id: str,
        *,
        embedding: list[float] | None,
        embedding_version: str | None,
    ) -> None:
        await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == image_id, ImageItem.embedding_status == "processing")
            .values(
                embedding=embedding,
                embedding_version=embedding_version,
                embedding_status="completed" if embedding is not None else "failed",
                embedding_completed_at=func.now(),
            )
        )
        await self.session.commit()

    async def claim_match_if_ready(self, image_id: str) -> bool:
        result = await self.session.execute(
            update(ImageItem)
            .where(
                ImageItem.id == image_id,
                ImageItem.status == "tagging",
                ImageItem.match_status == "pending",
                ImageItem.analysis_status.in_(("completed", "failed")),
                ImageItem.embedding_status.in_(("completed", "failed")),
            )
            .values(match_status="queued")
        )
        await self.session.commit()
        return result.rowcount == 1

    async def claim_match(self, image_id: str) -> ImageItem | None:
        result = await self.session.execute(
            update(ImageItem)
            .where(
                ImageItem.id == image_id,
                ImageItem.status == "tagging",
                ImageItem.match_status == "queued",
            )
            .values(match_status="processing", match_started_at=func.now())
            .returning(ImageItem.id)
        )
        if result.scalar_one_or_none() is None:
            await self.session.rollback()
            return None
        await self.session.commit()
        return await self.get_item(image_id)

    async def complete_match_stage(self, image_id: str) -> None:
        await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == image_id, ImageItem.match_status == "processing")
            .values(match_status="completed", match_completed_at=func.now())
        )
        await self.session.commit()

    async def list_jobs(self, limit: int, offset: int) -> tuple[int, list[ImageJob]]:
        total = await self.session.scalar(select(func.count()).select_from(ImageJob))
        result = await self.session.execute(
            select(ImageJob)
            .order_by(desc(ImageJob.created_at))
            .limit(limit)
            .offset(offset)
        )
        return int(total or 0), list(result.scalars().all())

    async def update_item(self, item: ImageItem, values: Mapping[str, object]) -> ImageItem:
        for name, value in values.items():
            setattr(item, name, value)
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def save_metadata_and_find_duplicates(
        self,
        item: ImageItem,
        values: Mapping[str, object],
        *,
        max_hamming_distance: int,
    ) -> tuple[ImageItem | None, ImageItem | None]:
        """Persist hashes and compare them under a per-job transaction lock."""
        await self.session.execute(select(func.pg_advisory_xact_lock(func.hashtext(item.job_id))))
        for name, value in values.items():
            setattr(item, name, value)
        await self.session.flush()

        exact = await self.session.scalar(
            select(ImageItem)
            .where(
                ImageItem.job_id == item.job_id,
                ImageItem.id != item.id,
                ImageItem.sha256 == item.sha256,
            )
            .limit(1)
        )
        similar = None
        if exact is None and item.phash:
            distance = func.bit_count(
                cast(ImageItem.phash, BIT(64)).bitwise_xor(
                    cast(literal(item.phash), BIT(64))
                )
            )
            similar = await self.session.scalar(
                select(ImageItem)
                .where(
                    ImageItem.job_id == item.job_id,
                    ImageItem.id != item.id,
                    ImageItem.phash.is_not(None),
                    distance <= max_hamming_distance,
                )
                .order_by(distance, ImageItem.created_at, ImageItem.id)
                .limit(1)
            )
        await self.session.commit()
        return exact, similar

    async def find_duplicate_item(
        self,
        *,
        job_id: str,
        image_id: str,
        sha256: str,
    ) -> ImageItem | None:
        candidates = await self.session.execute(
            select(ImageItem).where(
                ImageItem.job_id == job_id,
                ImageItem.id != image_id,
                ImageItem.sha256 == sha256,
            )
        )
        for candidate in candidates.scalars():
            if candidate.sha256 == sha256:
                return candidate
        return None

    async def find_similar_item(
        self,
        *,
        job_id: str,
        image_id: str,
        phash: str,
        max_hamming_distance: int,
    ) -> ImageItem | None:
        distance = func.bit_count(
            cast(ImageItem.phash, BIT(64)).bitwise_xor(cast(literal(phash), BIT(64)))
        )
        return await self.session.scalar(
            select(ImageItem)
            .where(
                ImageItem.job_id == job_id,
                ImageItem.id != image_id,
                ImageItem.phash.is_not(None),
                distance <= max_hamming_distance,
            )
            .order_by(distance, ImageItem.created_at, ImageItem.id)
            .limit(1)
        )

    async def start_item(self, item: ImageItem) -> None:
        await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == item.id, ImageItem.status == "queued")
            .values(status="analyzing")
        )
        await self.session.execute(
            update(ImageJob).where(ImageJob.id == item.job_id).values(status="analyzing")
        )
        await self.session.commit()

    async def reject_item(
        self,
        item: ImageItem,
        reject_codes: list[str],
        *,
        reason: str | None = None,
    ) -> None:
        if not await self._transition_to_terminal(item, "rejected", {"reject_codes": reject_codes}):
            return
        await self._upsert_result(
            item.id,
            {
                "decision": "rejected",
                "reject_codes_json": reject_codes,
                "reasons_json": [reason or "未通过装修照片基础质量标准"],
            },
        )
        await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == item.job_id)
            .values(
                processed_count=ImageJob.processed_count + 1,
                rejected_count=ImageJob.rejected_count + 1,
            )
        )
        item.preprocess_completed_at = item.preprocess_completed_at or func.now()
        await self.session.commit()
        await self.complete_job_if_finished(item.job_id)

    async def save_ai_processing(
        self,
        item: ImageItem,
        *,
        status: str,
        model_name: str,
        prompt_version: str,
        duration_ms: int | None = None,
        payload: Mapping[str, object] | None = None,
        error_message: str | None = None,
    ) -> None:
        await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == item.id)
            .values(
                ai_processing_status=status,
                ai_processing_json=dict(payload) if payload is not None else None,
                ai_processing_model=model_name,
                ai_processing_prompt_version=prompt_version,
                ai_processing_duration_ms=duration_ms,
                ai_processing_error=error_message,
            )
        )
        await self.session.commit()
        item.ai_processing_status = status
        item.ai_processing_json = dict(payload) if payload is not None else None
        item.ai_processing_model = model_name
        item.ai_processing_prompt_version = prompt_version
        item.ai_processing_duration_ms = duration_ms
        item.ai_processing_error = error_message

    async def fail_item(self, item: ImageItem, reason: str) -> None:
        if not await self._transition_to_terminal(item, "failed"):
            return
        await self._upsert_result(
            item.id,
            {
                "decision": "failed",
                "reasons_json": [reason],
            },
        )
        await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == item.job_id)
            .values(processed_count=ImageJob.processed_count + 1)
        )
        await self.session.commit()
        await self.complete_job_if_finished(item.job_id)

    async def complete_filter(
        self,
        item: ImageItem,
        *,
        final_score: float,
        reasons: list[str],
    ) -> bool:
        transitioned = await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == item.id, ImageItem.status == "analyzing")
            .values(status="filtered", preprocess_completed_at=func.now())
        )
        if transitioned.rowcount != 1:
            return False

        await self._upsert_result(
            item.id,
            {
                "decision": "filtered",
                "final_score": final_score,
                "reasons_json": reasons,
            },
        )
        await self.session.commit()
        item.status = "filtered"
        return True

    async def claim_enhancement_phase_if_filtering_complete(self, job_id: str) -> bool:
        pending = await self.session.scalar(
            select(func.count())
            .select_from(ImageItem)
            .where(
                ImageItem.job_id == job_id,
                ImageItem.status.in_(("queued", "analyzing")),
            )
        )
        if pending:
            return False

        transitioned = await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == job_id, ImageJob.status.in_(("queued", "analyzing")))
            .values(status="enhancing")
        )
        if transitioned.rowcount != 1:
            return False
        await self.session.commit()
        return True

    async def upsert_metrics(
        self,
        image_id: str,
        values: Mapping[str, object],
    ) -> ImageMetric:
        result = await self.session.execute(
            select(ImageMetric).where(ImageMetric.image_id == image_id)
        )
        metric = result.scalar_one_or_none()
        if metric is None:
            metric = ImageMetric(id=f"met_{image_id[4:]}", image_id=image_id, **values)
            self.session.add(metric)
        else:
            for name, value in values.items():
                setattr(metric, name, value)
        await self.session.commit()
        await self.session.refresh(metric)
        return metric

    async def start_enhancing(self, item: ImageItem) -> bool:
        transitioned = await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == item.id, ImageItem.status == "filtered")
            .values(status="enhancing")
        )
        if transitioned.rowcount != 1:
            return False

        await self.session.execute(
            update(ImageJob).where(ImageJob.id == item.job_id).values(status="enhancing")
        )
        await self.session.commit()
        return True

    async def complete_enhancement(
        self,
        item: ImageItem,
        *,
        enhanced_object_key: str,
        analysis_object_key: str,
        enhanced_metrics: Mapping[str, float],
        reasons: list[str],
    ) -> bool:
        transitioned = await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == item.id, ImageItem.status == "enhancing")
            .values(
                status="enhanced",
                analysis_object_key=analysis_object_key,
                enhance_completed_at=func.now(),
            )
        )
        if transitioned.rowcount != 1:
            return False

        stored_result = await self.session.execute(select(ImageResult).where(ImageResult.image_id == item.id))
        image_result = stored_result.scalar_one_or_none()
        previous_reasons = image_result.reasons_json if image_result else []
        await self._upsert_result(
            item.id,
            {
                "decision": "enhanced",
                "enhanced_object_key": enhanced_object_key,
                "enhanced_metrics_json": dict(enhanced_metrics),
                "reasons_json": [*(previous_reasons or []), *reasons],
            },
        )
        await self.session.commit()
        return True

    async def select_item(
        self,
        item: ImageItem,
        enhanced_object_key: str | None,
        final_score: float,
        reasons: list[str] | None = None,
        enhanced_metrics: Mapping[str, float] | None = None,
    ) -> None:
        if not await self._transition_to_terminal(item, "selected"):
            return
        await self._upsert_result(
            item.id,
            {
                "decision": "selected",
                "final_score": final_score,
                "enhanced_object_key": enhanced_object_key,
                "enhanced_metrics_json": dict(enhanced_metrics) if enhanced_metrics else None,
                "reasons_json": reasons or ["通过装修照片基础质量标准并完成自然美化"],
            },
        )
        await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == item.job_id)
            .values(
                processed_count=ImageJob.processed_count + 1,
                selected_count=ImageJob.selected_count + 1,
            )
        )
        await self.session.commit()
        await self.complete_job_if_finished(item.job_id)

    async def start_tagging(
        self,
        item: ImageItem,
        *,
        final_score: float,
        reasons: list[str],
        enhanced_object_key: str,
        enhanced_metrics: Mapping[str, float] | None,
        provider: str,
        model_name: str,
        prompt_version: str,
    ) -> bool:
        transitioned = await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == item.id, ImageItem.status == "enhanced")
            .values(
                status="tagging",
                analysis_status="pending",
                embedding_status="pending",
                match_status="pending",
            )
        )
        if transitioned.rowcount != 1:
            return False

        await self._upsert_result(
            item.id,
            {
                "decision": "tagging",
                "final_score": final_score,
                "enhanced_object_key": enhanced_object_key,
                "enhanced_metrics_json": dict(enhanced_metrics) if enhanced_metrics else None,
                "reasons_json": reasons,
            },
        )
        await self.upsert_ai_tag(
            image_id=item.id,
            source_object_key=enhanced_object_key,
            provider=provider,
            model_name=model_name,
            prompt_version=prompt_version,
            status="pending",
        )
        await self.session.execute(
            update(ImageJob).where(ImageJob.id == item.job_id).values(status="tagging")
        )
        await self.session.commit()
        return True

    async def upsert_ai_tag(
        self,
        *,
        image_id: str,
        source_object_key: str,
        provider: str,
        model_name: str,
        prompt_version: str,
        status: str,
        duration_ms: int | None = None,
        tag_json: Mapping[str, object] | None = None,
        raw_response_json: Mapping[str, object] | None = None,
        error_message: str | None = None,
    ) -> ImageAITag:
        result = await self.session.execute(select(ImageAITag).where(ImageAITag.image_id == image_id))
        tag = result.scalar_one_or_none()
        values = {
            "source_object_key": source_object_key,
            "provider": provider,
            "model_name": model_name,
            "prompt_version": prompt_version,
            "status": status,
            "duration_ms": duration_ms,
            "tag_json": dict(tag_json) if tag_json is not None else None,
            "raw_response_json": dict(raw_response_json) if raw_response_json is not None else None,
            "error_message": error_message,
        }
        if tag is None:
            tag = ImageAITag(id=f"tag_{image_id[4:]}", image_id=image_id, **values)
            self.session.add(tag)
        else:
            for name, value in values.items():
                setattr(tag, name, value)
        return tag

    async def complete_tagging(self, item: ImageItem, *, reason: str) -> None:
        transitioned = await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == item.id, ImageItem.status == "tagging")
            .values(status="selected")
        )
        if transitioned.rowcount != 1:
            return

        stored_result = await self.session.execute(select(ImageResult).where(ImageResult.image_id == item.id))
        image_result = stored_result.scalar_one_or_none()
        previous_reasons = image_result.reasons_json if image_result else []
        await self._upsert_result(
            item.id,
            {"decision": "selected", "reasons_json": [*(previous_reasons or []), reason]},
        )
        await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == item.job_id)
            .values(
                processed_count=ImageJob.processed_count + 1,
                selected_count=ImageJob.selected_count + 1,
            )
        )
        await self.session.commit()
        await self.complete_job_if_finished(item.job_id)

    async def mark_not_selected(self, item: ImageItem) -> None:
        transitioned = await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == item.id, ImageItem.status == "filtered")
            .values(status="not_selected")
        )
        if transitioned.rowcount != 1:
            await self.session.rollback()
            return
        await self._upsert_result(
            item.id,
            {
                "decision": "not_selected",
                "reasons_json": ["质量合格，但未进入本任务的优选数量范围"],
            },
        )
        await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == item.job_id)
            .values(
                processed_count=ImageJob.processed_count + 1,
                not_selected_count=ImageJob.not_selected_count + 1,
            )
        )
        await self.session.commit()
        await self.complete_job_if_finished(item.job_id)

    async def list_filtered_items_by_score(self, job_id: str) -> list[ImageItem]:
        result = await self.session.execute(
            select(ImageItem)
            .join(ImageResult, ImageResult.image_id == ImageItem.id)
            .where(ImageItem.job_id == job_id, ImageItem.status == "filtered")
            .order_by(ImageResult.final_score.desc(), ImageItem.created_at)
            .options(selectinload(ImageItem.metric), selectinload(ImageItem.result))
        )
        return list(result.scalars())

    async def claim_ranking_if_filtering_complete(self, job_id: str) -> bool:
        pending = await self.session.scalar(
            select(func.count()).select_from(ImageItem).where(
                ImageItem.job_id == job_id,
                ImageItem.status.in_(("queued", "analyzing")),
            )
        )
        if pending:
            return False
        result = await self.session.execute(
            update(ImageJob)
            .where(
                ImageJob.id == job_id,
                ImageJob.cancel_requested_at.is_(None),
                ImageJob.status == "processing",
            )
            .values(status="ranking")
        )
        await self.session.commit()
        return result.rowcount == 1

    async def finish_ranking(self, job_id: str) -> None:
        await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == job_id, ImageJob.status == "ranking")
            .values(status="processing")
        )
        await self.session.commit()

    async def get_progress_snapshot(self, job_id: str) -> JobProgressSnapshot | None:
        job_row = (
            await self.session.execute(
                select(
                    ImageJob.id,
                    ImageJob.status,
                    ImageJob.total_count,
                    ImageJob.processed_count,
                    ImageJob.selected_count,
                    ImageJob.rejected_count,
                    ImageJob.not_selected_count,
                ).where(ImageJob.id == job_id)
            )
        ).one_or_none()
        if job_row is None:
            return None
        rows = await self.session.execute(
            select(
                ImageItem.status,
                ImageItem.analysis_status,
                ImageItem.embedding_status,
                ImageItem.match_status,
                func.count(ImageItem.id),
            )
            .where(ImageItem.job_id == job_id)
            .group_by(
                ImageItem.status,
                ImageItem.analysis_status,
                ImageItem.embedding_status,
                ImageItem.match_status,
            )
        )
        counts = {
            "waiting": 0,
            "filtering": 0,
            "beautifying": 0,
            "content_analysis": 0,
            "matching": 0,
            "completed": 0,
            "rejected": 0,
            "not_selected": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for status, analysis_status, embedding_status, match_status, count in rows:
            count = int(count)
            if status == "queued":
                counts["waiting"] += count
            elif status == "analyzing":
                counts["filtering"] += count
            elif status in {"filtered", "enhancing", "enhanced"}:
                counts["beautifying"] += count
            elif status == "tagging":
                if analysis_status in {"pending", "processing"} or embedding_status in {"pending", "processing"}:
                    counts["content_analysis"] += count
                elif match_status in {"pending", "queued", "processing"}:
                    counts["matching"] += count
            elif status == "selected":
                counts["completed"] += count
            elif status == "rejected":
                counts["rejected"] += count
            elif status == "not_selected":
                counts["not_selected"] += count
            elif status == "failed":
                counts["failed"] += count
            elif status == "cancelled":
                counts["cancelled"] += count
        return JobProgressSnapshot(*job_row, stage_counts=counts)

    async def list_result_items(
        self,
        job_id: str,
        *,
        limit: int,
        offset: int,
        decision: str | None,
    ) -> tuple[int, list[ImageItem]]:
        filters = [ImageItem.job_id == job_id, ImageItem.result.has()]
        if decision:
            filters.append(ImageResult.decision == decision)
        total_statement = (
            select(func.count(ImageItem.id))
            .outerjoin(ImageResult, ImageResult.image_id == ImageItem.id)
            .where(*filters)
        )
        total = int(await self.session.scalar(total_statement) or 0)
        result = await self.session.execute(
            select(ImageItem)
            .outerjoin(ImageResult, ImageResult.image_id == ImageItem.id)
            .where(*filters)
            .order_by(ImageItem.created_at, ImageItem.id)
            .limit(limit)
            .offset(offset)
            .options(
                selectinload(ImageItem.metric),
                selectinload(ImageItem.result),
                selectinload(ImageItem.ai_tag),
                selectinload(ImageItem.similarity_match),
            )
        )
        return total, list(result.scalars().unique())

    async def cancel_job(self, job_id: str) -> bool:
        job_result = await self.session.execute(
            update(ImageJob)
            .where(
                ImageJob.id == job_id,
                ImageJob.status.not_in(("completed", "partial_failed", "failed", "cancelled")),
            )
            .values(status="cancelled", cancel_requested_at=func.now(), completed_at=func.now())
        )
        if job_result.rowcount != 1:
            await self.session.rollback()
            return False
        terminal = ("selected", "rejected", "failed", "not_selected", "cancelled")
        pending_count = int(
            await self.session.scalar(
                select(func.count()).select_from(ImageItem).where(
                    ImageItem.job_id == job_id, ImageItem.status.not_in(terminal)
                )
            )
            or 0
        )
        await self.session.execute(
            update(ImageItem)
            .where(ImageItem.job_id == job_id, ImageItem.status.not_in(terminal))
            .values(status="cancelled")
        )
        await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == job_id)
            .values(processed_count=ImageJob.processed_count + pending_count)
        )
        await self._mark_callback_pending(job_id)
        await self.session.commit()
        return True

    async def retry_failed_item(self, job_id: str, image_id: str) -> bool:
        result = await self.session.execute(
            update(ImageItem)
            .where(
                ImageItem.id == image_id,
                ImageItem.job_id == job_id,
                ImageItem.status == "failed",
            )
            .values(
                status="queued",
                reject_codes=None,
                analysis_status=None,
                embedding_status=None,
                match_status=None,
                embedding=None,
                embedding_version=None,
                ai_processing_status=None,
                ai_processing_json=None,
                ai_processing_model=None,
                ai_processing_prompt_version=None,
                ai_processing_duration_ms=None,
                ai_processing_error=None,
                preprocess_started_at=None,
                preprocess_completed_at=None,
                enhance_started_at=None,
                enhance_completed_at=None,
                analysis_started_at=None,
                analysis_completed_at=None,
                embedding_started_at=None,
                embedding_completed_at=None,
                match_started_at=None,
                match_completed_at=None,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False
        await self.session.execute(delete(ImageResult).where(ImageResult.image_id == image_id))
        await self.session.execute(delete(ImageAITag).where(ImageAITag.image_id == image_id))
        await self.session.execute(
            delete(ImageSimilarityMatch).where(ImageSimilarityMatch.image_id == image_id)
        )
        await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == job_id)
            .values(
                status="processing",
                completed_at=None,
                processed_count=func.greatest(ImageJob.processed_count - 1, 0),
                callback_status=None,
                callback_attempts=0,
                callback_next_attempt_at=None,
                callback_last_attempt_at=None,
                callback_delivered_at=None,
                callback_last_error=None,
            )
        )
        await self.session.commit()
        return True

    async def _upsert_result(self, image_id: str, values: Mapping[str, object]) -> ImageResult:
        result = await self.session.execute(select(ImageResult).where(ImageResult.image_id == image_id))
        image_result = result.scalar_one_or_none()
        if image_result is None:
            image_result = ImageResult(id=f"res_{image_id[4:]}", image_id=image_id, **values)
            self.session.add(image_result)
        else:
            for name, value in values.items():
                setattr(image_result, name, value)
        return image_result

    async def _transition_to_terminal(
        self,
        item: ImageItem,
        status: str,
        values: Mapping[str, object] | None = None,
    ) -> bool:
        result = await self.session.execute(
            update(ImageItem)
            .where(
                ImageItem.id == item.id,
                ImageItem.status.in_(("queued", "analyzing", "filtered", "enhancing", "enhanced")),
            )
            .values(status=status, **(dict(values) if values else {}))
        )
        return result.rowcount == 1

    async def complete_job_if_finished(self, job_id: str) -> bool:
        counters = (
            await self.session.execute(
                select(ImageJob.processed_count, ImageJob.total_count, ImageJob.status).where(
                    ImageJob.id == job_id
                )
            )
        ).one_or_none()
        if counters is None or counters.processed_count < counters.total_count:
            return False

        failed_item = await self.session.scalar(
            select(ImageItem.id)
            .where(ImageItem.job_id == job_id, ImageItem.status == "failed")
            .limit(1)
        )
        result = await self.session.execute(
            update(ImageJob)
            .where(
                ImageJob.id == job_id,
                ImageJob.processed_count >= ImageJob.total_count,
                ImageJob.status.in_(("queued", "processing", "ranking", "analyzing", "enhancing", "tagging")),
            )
            .values(
                status="partial_failed" if failed_item is not None else "completed",
                completed_at=func.now(),
            )
        )
        if result.rowcount == 1:
            await self._mark_callback_pending(job_id)
        await self.session.commit()
        return result.rowcount == 1

    async def list_callback_jobs_due(self, *, limit: int, lease_seconds: int) -> list[str]:
        now = datetime.now(UTC)
        lease_expired_at = now - timedelta(seconds=max(1, lease_seconds))
        result = await self.session.execute(
            select(ImageJob.id)
            .where(
                ImageJob.callback_url.is_not(None),
                ImageJob.status.in_(("completed", "partial_failed", "failed", "cancelled")),
                or_(
                    (
                        (ImageJob.callback_status == "pending")
                        & or_(
                            ImageJob.callback_next_attempt_at.is_(None),
                            ImageJob.callback_next_attempt_at <= now,
                        )
                    ),
                    (
                        (ImageJob.callback_status == "delivering")
                        & or_(
                            ImageJob.callback_last_attempt_at.is_(None),
                            ImageJob.callback_last_attempt_at <= lease_expired_at,
                        )
                    ),
                ),
            )
            .order_by(ImageJob.callback_next_attempt_at, ImageJob.completed_at, ImageJob.id)
            .limit(max(1, limit))
        )
        return list(result.scalars())

    async def claim_callback_delivery(self, job_id: str, *, lease_seconds: int) -> CallbackJob | None:
        now = datetime.now(UTC)
        lease_expired_at = now - timedelta(seconds=max(1, lease_seconds))
        result = await self.session.execute(
            update(ImageJob)
            .where(
                ImageJob.id == job_id,
                ImageJob.callback_url.is_not(None),
                ImageJob.status.in_(("completed", "partial_failed", "failed", "cancelled")),
                or_(
                    (
                        (ImageJob.callback_status == "pending")
                        & or_(
                            ImageJob.callback_next_attempt_at.is_(None),
                            ImageJob.callback_next_attempt_at <= now,
                        )
                    ),
                    (
                        (ImageJob.callback_status == "delivering")
                        & or_(
                            ImageJob.callback_last_attempt_at.is_(None),
                            ImageJob.callback_last_attempt_at <= lease_expired_at,
                        )
                    ),
                ),
            )
            .values(
                callback_status="delivering",
                callback_attempts=ImageJob.callback_attempts + 1,
                callback_last_attempt_at=now,
                callback_last_error=None,
            )
            .returning(
                ImageJob.id,
                ImageJob.callback_url,
                ImageJob.status,
                ImageJob.completed_at,
                ImageJob.callback_attempts,
            )
        )
        row = result.one_or_none()
        if row is None:
            await self.session.rollback()
            return None
        await self.session.commit()
        return CallbackJob(
            id=row.id,
            callback_url=row.callback_url,
            status=row.status,
            completed_at=row.completed_at,
            attempts=row.callback_attempts,
        )

    async def mark_callback_delivered(self, job_id: str) -> None:
        await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == job_id, ImageJob.callback_status == "delivering")
            .values(
                callback_status="delivered",
                callback_next_attempt_at=None,
                callback_delivered_at=func.now(),
                callback_last_error=None,
            )
        )
        await self.session.commit()

    async def mark_callback_failed(
        self,
        job_id: str,
        *,
        error: str,
        retry_at: datetime | None,
    ) -> None:
        await self.session.execute(
            update(ImageJob)
            .where(ImageJob.id == job_id, ImageJob.callback_status == "delivering")
            .values(
                callback_status="pending" if retry_at is not None else "failed",
                callback_next_attempt_at=retry_at,
                callback_last_error=error[:1000],
            )
        )
        await self.session.commit()

    async def _mark_callback_pending(self, job_id: str) -> None:
        await self.session.execute(
            update(ImageJob)
            .where(
                ImageJob.id == job_id,
                ImageJob.callback_url.is_not(None),
                ImageJob.callback_url != "",
            )
            .values(
                callback_status="pending",
                callback_attempts=0,
                callback_next_attempt_at=func.now(),
                callback_last_attempt_at=None,
                callback_delivered_at=None,
                callback_last_error=None,
            )
        )
