from collections.abc import Mapping

from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.image_ai_tag import ImageAITag
from src.models.image_item import ImageItem
from src.models.image_job import ImageJob
from src.models.image_metric import ImageMetric
from src.models.image_result import ImageResult


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
        candidates = await self.session.execute(
            select(ImageItem).where(
                ImageItem.job_id == job_id,
                ImageItem.id != image_id,
                ImageItem.phash.is_not(None),
            )
        )
        for candidate in candidates.scalars():
            if candidate.phash and _hamming_distance(candidate.phash, phash) <= max_hamming_distance:
                return candidate
        return None

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

    async def reject_item(self, item: ImageItem, reject_codes: list[str]) -> None:
        if not await self._transition_to_terminal(item, "rejected", {"reject_codes": reject_codes}):
            return
        await self._upsert_result(
            item.id,
            {
                "decision": "rejected",
                "reject_codes_json": reject_codes,
                "reasons_json": ["未通过装修照片基础质量标准"],
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
        await self.session.commit()

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
            .values(status="filtered")
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
        enhanced_metrics: Mapping[str, float],
        reasons: list[str],
    ) -> bool:
        transitioned = await self.session.execute(
            update(ImageItem)
            .where(ImageItem.id == item.id, ImageItem.status == "enhancing")
            .values(status="enhanced")
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
            .values(status="tagging")
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

    async def complete_job_if_finished(self, job_id: str) -> None:
        job = await self.get(job_id)
        if job is None:
            return

        # Other image tasks update the counters in separate database sessions.
        # Refresh avoids completing against the value cached when this task began.
        await self.session.refresh(job)
        if job.processed_count < job.total_count:
            return

        failed_item = await self.session.scalar(
            select(ImageItem.id)
            .where(ImageItem.job_id == job_id, ImageItem.status == "failed")
            .limit(1)
        )
        await self.session.execute(
            update(ImageJob)
            .where(
                ImageJob.id == job_id,
                ImageJob.processed_count >= ImageJob.total_count,
                ImageJob.status.in_(("queued", "analyzing", "enhancing", "tagging")),
            )
            .values(
                status="partial_failed" if failed_item is not None else "completed",
                completed_at=func.now(),
            )
        )
        await self.session.commit()


def _hamming_distance(left: str, right: str) -> int:
    if len(left) != len(right):
        return max(len(left), len(right))
    return sum(left_bit != right_bit for left_bit, right_bit in zip(left, right, strict=True))
