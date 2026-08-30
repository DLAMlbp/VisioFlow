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
    ImageAuditDimensionResponse,
    ImageItemStatus,
    ImageJobHistoryItemResponse,
    ImageJobHistoryResponse,
    ImageJobProgressResponse,
    ImageJobResultItemResponse,
    ImageJobResultsResponse,
    ImageMetricsResponse,
    ImageSimilarityResultResponse,
    JobStatus,
)
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.processing_vision import (
    filter_dimensions_from_processing_json,
    selected_standard_from_processing_json,
)
from src.services.jobs.callback_security import (
    CallbackConfigurationError,
    validate_callback_destination,
)
from src.services.jobs.dispatch import JobDispatchTaskPublisher, TaskPublisher
from src.services.jobs.ids import build_image_id, build_job_id
from src.services.managed_profiles import (
    ManagedProfileService,
    neutral_beautify_snapshot,
    standards_from_snapshots,
)
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
        task_publisher: TaskPublisher | None = None,
        profile_loader: ProfileLoader | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.task_publisher = task_publisher
        self.profile_loader = profile_loader

    async def create_job(self, payload: CreateImageJobRequest) -> CreateImageJobResponse:
        if len(payload.images) > self.settings.max_images_per_job:
            raise InvalidJobRequest(f"单个 Job 最多支持 {self.settings.max_images_per_job} 张图片")
        if payload.callback_url:
            try:
                validate_callback_destination(
                    str(payload.callback_url),
                    production=self.settings.app_env == "production",
                    allowed_hosts=self.settings.callback_allowed_hosts,
                )
            except CallbackConfigurationError as exc:
                raise InvalidJobRequest(str(exc)) from exc
        filter_snapshot = None
        beautify_snapshot = (
            None if payload.beautify_enabled else neutral_beautify_snapshot()
        )
        standard_snapshots = None
        if self.profile_loader is not None:
            try:
                if hasattr(self.repository, "session"):
                    manager = ManagedProfileService(self.repository.session, self.settings)
                    if payload.filter_enabled and payload.processing_standards:
                        resolved = await manager.resolve_standards(payload.processing_standards)
                        standard_snapshots = [snapshot for _, snapshot in resolved]
                    elif payload.filter_enabled:
                        _, filter_snapshot = await manager.resolve_filter(payload.filter_profile or "")
                    if payload.beautify_enabled:
                        _, beautify_snapshot = await manager.resolve_beautify(
                            payload.beautify_profile or ""
                        )
                    else:
                        beautify_snapshot = neutral_beautify_snapshot()
                else:
                    raise RuntimeError("托管处理标准需要数据库会话")
                if payload.similarity_enabled:
                    self.profile_loader.get_similarity_profile(payload.similarity_profile)
            except ProfileNotFoundError as exc:
                raise InvalidJobRequest(exc.args[0]) from exc

        job = ImageJob(
            id=build_job_id(),
            status=JobStatus.QUEUED.value,
            filter_profile_id=(
                payload.filter_profile or ("conditional_standard_v1" if payload.filter_enabled else "system_passthrough")
            ),
            beautify_profile_id=(
                payload.beautify_profile or ("conditional_standard_v1" if payload.beautify_enabled else "system_delivery")
            ),
            filter_profile_snapshot=filter_snapshot,
            beautify_profile_snapshot=beautify_snapshot,
            processing_standard_snapshots=standard_snapshots,
            filter_enabled=payload.filter_enabled,
            beautify_enabled=payload.beautify_enabled,
            similarity_enabled=payload.similarity_enabled,
            similarity_profile_id=payload.similarity_profile,
            unmatched_standard_policy=payload.unmatched_standard_policy,
            ai_tagging_model=self.settings.ai_tagging_model if self.settings.ai_tagging_enabled else None,
            enhance_level=payload.enhance_level,
            max_selected=payload.max_selected,
            total_count=len(payload.images),
            processed_count=0,
            selected_count=0,
            rejected_count=0,
            not_selected_count=0,
            dispatch_cursor=0,
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
            self.task_publisher.publish(created.id)
        return CreateImageJobResponse(
            job_id=created.id,
            status=JobStatus(created.status),
            total=created.total_count,
        )

    async def get_progress(self, job_id: str) -> ImageJobProgressResponse:
        snapshot = await self.repository.get_progress_snapshot(job_id)
        if snapshot is None:
            raise JobNotFound("Job 不存在")

        progress = self._calculate_progress(
            snapshot.stage_counts, snapshot.total_count, snapshot.status
        )
        return ImageJobProgressResponse(
            job_id=snapshot.id,
            status=JobStatus(snapshot.status),
            progress=progress,
            total=snapshot.total_count,
            processed=snapshot.processed_count,
            selected=snapshot.selected_count,
            rejected=snapshot.rejected_count,
            not_selected=snapshot.not_selected_count,
            tagging=(
                snapshot.stage_counts.get("content_analysis", 0)
                + snapshot.stage_counts.get("matching", 0)
            ),
            stage_counts=snapshot.stage_counts,
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
                    not_selected=job.not_selected_count or 0,
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

    async def get_results(
        self,
        job_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        decision: str | None = None,
    ) -> ImageJobResultsResponse:
        snapshot = await self.repository.get_progress_snapshot(job_id)
        if snapshot is None:
            raise JobNotFound("Job 不存在")
        result_total, result_items = await self.repository.list_result_items(
            job_id, limit=limit, offset=offset, decision=decision
        )
        job_config = (
            await self.repository.get_config(job_id)
            if hasattr(self.repository, "get_config")
            else None
        )
        standard_names = {
            standard.id: standard.name or standard.description
            for standard in standards_from_snapshots(
                job_config.processing_standard_snapshots if job_config else None
            )
        }
        images = []
        for item in result_items:
            result = item.result
            metric = item.metric
            if result is None:
                continue
            selected_standard_id, activation_reason = selected_standard_from_processing_json(
                item.ai_processing_json
            )
            audit_dimensions = filter_dimensions_from_processing_json(
                item.ai_processing_json
            )
            images.append(
                ImageJobResultItemResponse(
                    image_id=item.id,
                    decision=ImageItemStatus(result.decision),
                    score=result.final_score,
                    original_object_key=item.object_key,
                    enhanced_object_key=result.enhanced_object_key,
                    original_preview_object_key=getattr(item, "thumbnail_object_key", None),
                    enhanced_preview_object_key=getattr(item, "analysis_object_key", None),
                    files_expired=item.purged_at is not None,
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
                    tagging_result=(
                        ImageSimilarityResultResponse(
                            decision=item.similarity_match.decision,
                            tags=item.similarity_match.matched_tags_snapshot or [],
                            matched_asset_id=item.similarity_match.matched_asset_id,
                            similarity=item.similarity_match.similarity_score,
                            final_score=item.similarity_match.final_score,
                            message=item.similarity_match.message,
                        )
                        if item.similarity_match is not None
                        else None
                    ),
                    processing_standard_id=selected_standard_id,
                    processing_standard_name=standard_names.get(selected_standard_id or ""),
                    activation_reason=activation_reason,
                    audit_dimensions=[
                        ImageAuditDimensionResponse(
                            dimension=dimension.dimension,
                            passed=dimension.passed,
                            reason=dimension.reason,
                        )
                        for dimension in audit_dimensions
                    ],
                )
            )
        return ImageJobResultsResponse(
            job_id=snapshot.id,
            total=snapshot.total_count,
            selected=snapshot.selected_count,
            rejected=snapshot.rejected_count,
            not_selected=snapshot.not_selected_count,
            result_total=result_total,
            limit=limit,
            offset=offset,
            images=images,
        )

    @staticmethod
    def _calculate_progress(stage_counts: dict[str, int], total: int, status: str) -> int:
        if status in {
            JobStatus.COMPLETED.value,
            JobStatus.PARTIAL_FAILED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
        }:
            return 100
        if total <= 0:
            return 0
        terminal = sum(
            stage_counts.get(key, 0)
            for key in ("completed", "rejected", "not_selected", "failed", "cancelled")
        )
        weighted = (
            stage_counts.get("filtering", 0) * 0.15
            + stage_counts.get("beautifying", 0) * 0.38
            + stage_counts.get("content_analysis", 0) * 0.68
            + stage_counts.get("matching", 0) * 0.90
            + terminal
        )
        return min(99, max(0, int(weighted / total * 100)))

    async def cancel_job(self, job_id: str) -> ImageJobProgressResponse:
        if not await self.repository.cancel_job(job_id):
            if await self.repository.get_config(job_id) is None:
                raise JobNotFound("Job 不存在")
            raise InvalidJobRequest("任务已经结束，不能取消")
        return await self.get_progress(job_id)

    async def retry_failed_image(self, job_id: str, image_id: str) -> ImageJobProgressResponse:
        if not await self.repository.retry_failed_item(job_id, image_id):
            raise InvalidJobRequest("仅支持重试当前任务中处理失败的图片")
        from src.services.jobs.dispatch import MetadataTaskPublisher

        MetadataTaskPublisher().publish(image_id)
        return await self.get_progress(job_id)


def get_job_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ImageJobService:
    settings = load_ai_model_settings(settings)
    return ImageJobService(
        ImageJobRepository(session),
        settings,
        task_publisher=JobDispatchTaskPublisher(),
        profile_loader=ProfileLoader(settings),
    )
