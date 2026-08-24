from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.exceptions import AppError
from src.db.session import get_db_session
from src.models.image_item import ImageItem
from src.models.image_job import ImageJob
from src.repositories.jobs import ImageJobRepository
from src.schemas.jobs import (
    CreateImageJobRequest,
    CreateImageJobResponse,
    ImageAITagsResponse,
    ImageItemStatus,
    ImageJobHistoryItemResponse,
    ImageJobHistoryResponse,
    ImageJobProgressResponse,
    ImageJobResultItemResponse,
    ImageJobResultsResponse,
    ImageMetricsResponse,
    JobStatus,
)
from src.services.ai_model_config import load_ai_model_settings
from src.services.jobs.dispatch import CeleryMetadataTaskPublisher, MetadataTaskPublisher
from src.services.jobs.ids import build_image_id, build_job_id
from src.services.profiles import ProfileLoader, ProfileNotFoundError


class JobNotFound(AppError):
    code = "JOB_NOT_FOUND"


class InvalidJobRequest(AppError):
    code = "INVALID_JOB_REQUEST"


class ImageJobService:
    def __init__(
        self,
        repository: ImageJobRepository,
        settings: Settings,
        task_publisher: MetadataTaskPublisher | None = None,
        profile_loader: ProfileLoader | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.task_publisher = task_publisher
        self.profile_loader = profile_loader

    async def create_job(self, payload: CreateImageJobRequest) -> CreateImageJobResponse:
        if len(payload.images) > self.settings.max_images_per_job:
            raise InvalidJobRequest(f"单个 Job 最多支持 {self.settings.max_images_per_job} 张图片")
        if self.profile_loader is not None:
            try:
                self.profile_loader.get_filter_profile(payload.filter_profile)
                self.profile_loader.get_beautify_profile(payload.beautify_profile)
            except ProfileNotFoundError as exc:
                raise InvalidJobRequest(exc.args[0]) from exc

        job = ImageJob(
            id=build_job_id(),
            status=JobStatus.QUEUED.value,
            filter_profile_id=payload.filter_profile,
            beautify_profile_id=payload.beautify_profile,
            ai_tagging_model=self.settings.ai_tagging_model if self.settings.ai_tagging_enabled else None,
            enhance_level=payload.enhance_level,
            max_selected=payload.max_selected,
            total_count=len(payload.images),
            processed_count=0,
            selected_count=0,
            rejected_count=0,
            callback_url=str(payload.callback_url) if payload.callback_url else None,
        )
        items = [
            ImageItem(
                id=build_image_id(),
                job_id=job.id,
                object_key=image.object_key,
                status=ImageItemStatus.QUEUED.value,
            )
            for image in payload.images
        ]

        created = await self.repository.create(job, items)
        if self.task_publisher is not None:
            for item in items:
                self.task_publisher.publish(item.id)
        return CreateImageJobResponse(
            job_id=created.id,
            status=JobStatus(created.status),
            total=created.total_count,
        )

    async def get_progress(self, job_id: str) -> ImageJobProgressResponse:
        job = await self.repository.get(job_id)
        if job is None:
            raise JobNotFound("Job 不存在")

        progress = self._calculate_progress(job)
        return ImageJobProgressResponse(
            job_id=job.id,
            status=JobStatus(job.status),
            progress=progress,
            total=job.total_count,
            processed=job.processed_count,
            selected=job.selected_count,
            rejected=job.rejected_count,
            tagging=sum(item.status == ImageItemStatus.TAGGING.value for item in job.items),
        )

    async def list_history(self, limit: int, offset: int) -> ImageJobHistoryResponse:
        total, jobs = await self.repository.list_jobs(limit, offset)
        return ImageJobHistoryResponse(
            total=total,
            limit=limit,
            offset=offset,
            items=[
                ImageJobHistoryItemResponse(
                    job_id=job.id,
                    status=JobStatus(job.status),
                    total=job.total_count,
                    processed=job.processed_count,
                    selected=job.selected_count,
                    rejected=job.rejected_count,
                    ai_tagging_model=(
                        job.ai_tagging_model
                        if job.ai_tagging_model is not None
                        else self.settings.ai_tagging_model if self.settings.ai_tagging_enabled else None
                    ),
                    created_at=job.created_at,
                    completed_at=job.completed_at,
                )
                for job in jobs
            ],
        )

    async def get_results(self, job_id: str) -> ImageJobResultsResponse:
        job = await self.repository.get(job_id)
        if job is None:
            raise JobNotFound("Job 不存在")

        images = []
        for item in job.items:
            result = item.result
            metric = item.metric
            if result is None:
                continue
            images.append(
                ImageJobResultItemResponse(
                    image_id=item.id,
                    decision=ImageItemStatus(result.decision),
                    score=result.final_score,
                    original_object_key=item.object_key,
                    enhanced_object_key=result.enhanced_object_key,
                    reject_codes=result.reject_codes_json or [],
                    reasons=result.reasons_json or [],
                    metrics=(
                        ImageMetricsResponse(
                            sharpness=metric.sharpness_score,
                            exposure=metric.exposure_score,
                            contrast=metric.contrast_score,
                            noise=metric.noise_score,
                        )
                        if metric is not None
                        else None
                    ),
                    enhanced_metrics=(
                        ImageMetricsResponse(**result.enhanced_metrics_json)
                        if result.enhanced_metrics_json is not None
                        else None
                    ),
                    ai_tags=(
                        ImageAITagsResponse(
                            status=item.ai_tag.status,
                            summary=(item.ai_tag.tag_json or {}).get("summary"),
                            tags=(item.ai_tag.tag_json or {}).get("tags", []),
                            categories=(item.ai_tag.tag_json or {}).get("categories", {}),
                            candidate_tags=(item.ai_tag.tag_json or {}).get("candidate_tags", []),
                            confidence=(item.ai_tag.tag_json or {}).get("confidence"),
                            risks=(item.ai_tag.tag_json or {}).get("risks", []),
                            source_object_key=item.ai_tag.source_object_key,
                            error_message=item.ai_tag.error_message,
                        )
                        if item.ai_tag is not None
                        else None
                    ),
                )
            )
        return ImageJobResultsResponse(
            job_id=job.id,
            total=job.total_count,
            selected=job.selected_count,
            rejected=job.rejected_count,
            images=images,
        )

    @staticmethod
    def _calculate_progress(job: ImageJob) -> int:
        if job.status in {
            JobStatus.COMPLETED.value,
            JobStatus.PARTIAL_FAILED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
        }:
            return 100
        if job.total_count <= 0:
            return 0

        statuses = [item.status for item in job.items]
        if not statuses:
            return min(99, int((job.processed_count / job.total_count) * 100))
        if job.status in {JobStatus.QUEUED.value, JobStatus.ANALYZING.value}:
            filter_completed = sum(status not in {"queued", "analyzing"} for status in statuses)
            return int((filter_completed / job.total_count) * 35)
        if job.status == JobStatus.ENHANCING.value:
            enhancement_completed = sum(
                status in {"enhanced", "tagging", "selected", "rejected", "failed"}
                for status in statuses
            )
            return 35 + int((enhancement_completed / job.total_count) * 40)
        if job.status == JobStatus.TAGGING.value:
            tagging_completed = sum(status in {"selected", "rejected", "failed"} for status in statuses)
            return 75 + int((tagging_completed / job.total_count) * 24)
        return 0


def get_job_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ImageJobService:
    settings = load_ai_model_settings(settings)
    return ImageJobService(
        ImageJobRepository(session),
        settings,
        task_publisher=CeleryMetadataTaskPublisher(),
        profile_loader=ProfileLoader(settings),
    )
