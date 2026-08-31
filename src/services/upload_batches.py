from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.exceptions import AppError, InvalidUploadRequest
from src.db.session import get_db_session
from src.models.image_item import ImageItem
from src.models.image_job import ImageJob
from src.models.upload_batch import UploadBatch, UploadBatchItem
from src.repositories.upload_batches import UploadBatchRepository
from src.schemas.jobs import JobStatus
from src.schemas.upload_batches import (
    CompleteUploadBatchRequest,
    CompleteUploadBatchResponse,
    CreateUploadBatchRequest,
    CreateUploadBatchResponse,
    UploadBatchItemResponse,
)
from src.schemas.uploads import PresignedUploadRequest
from src.services.ai_model_config import load_ai_model_settings
from src.services.jobs.callback_security import (
    CallbackConfigurationError,
    validate_callback_destination,
)
from src.services.jobs.dispatch import JobDispatchTaskPublisher
from src.services.jobs.ids import (
    build_image_id,
    build_job_id,
    build_upload_batch_id,
    build_upload_batch_item_id,
)
from src.services.managed_profiles import ManagedProfileService, neutral_beautify_snapshot
from src.services.profiles import ProfileLoader, ProfileNotFoundError
from src.services.storage.factory import get_storage_provider
from src.services.storage.keys import build_upload_object_key, validate_upload_request


class UploadBatchNotFound(AppError):
    code = "UPLOAD_BATCH_NOT_FOUND"


class InvalidUploadBatch(AppError):
    code = "INVALID_UPLOAD_BATCH"


class UploadBatchService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repository = UploadBatchRepository(session)
        self.storage = get_storage_provider()

    async def create(self, payload: CreateUploadBatchRequest) -> CreateUploadBatchResponse:
        if len(payload.files) > self.settings.max_upload_batch_size:
            raise InvalidUploadBatch(
                f"单个上传批次最多支持 {self.settings.max_upload_batch_size} 张图片"
            )
        if payload.filter_route is None:
            raise InvalidUploadBatch("新任务必须使用完工分类和双路由过滤配置")
        if payload.callback_url:
            try:
                validate_callback_destination(
                    str(payload.callback_url),
                    production=self.settings.app_env == "production",
                    allowed_hosts=self.settings.callback_allowed_hosts,
                )
            except CallbackConfigurationError as exc:
                raise InvalidUploadBatch(str(exc)) from exc
        try:
            loader = ProfileLoader(self.settings)
            manager = ManagedProfileService(self.session, self.settings)
            filter_snapshot = None
            beautify_snapshot = None
            standard_snapshots = None
            routing_mode = "completion"
            completion_profile_id = None
            completion_snapshot = None
            completed_filter_profile_id = None
            completed_filter_snapshot = None
            non_completed_filter_profile_id = None
            non_completed_filter_snapshot = None
            routing_policy = None
            route = payload.filter_route
            completion, completed, non_completed = (
                await manager.resolve_routing_profiles(
                    completion_profile_id=route.completion_profile,
                    completed_filter_profile_id=route.completed_filter_profile,
                    non_completed_filter_profile_id=route.non_completed_filter_profile,
                )
            )
            completion_profile_id = completion[0].id
            completion_snapshot = completion[1]
            completed_filter_profile_id = completed[0].id
            completed_filter_snapshot = completed[1]
            non_completed_filter_profile_id = non_completed[0].id
            non_completed_filter_snapshot = non_completed[1]
            routing_policy = route.policy.model_dump(mode="json")
            standard_snapshots = [completed[1], non_completed[1]]
            if payload.beautify_enabled:
                _, beautify_snapshot = await manager.resolve_beautify(
                    payload.beautify_profile or ""
                )
            else:
                beautify_snapshot = neutral_beautify_snapshot()
            if payload.similarity_enabled:
                loader.get_similarity_profile(payload.similarity_profile)
        except ProfileNotFoundError as exc:
            raise InvalidUploadBatch(exc.args[0]) from exc

        now = datetime.now(UTC)
        batch = UploadBatch(
            id=build_upload_batch_id(),
            status="registered",
            filter_profile_id=(
                payload.filter_profile or "completion_routing_v1"
            ),
            beautify_profile_id=(
                payload.beautify_profile or ("conditional_standard_v1" if payload.beautify_enabled else "system_delivery")
            ),
            filter_profile_snapshot=filter_snapshot,
            beautify_profile_snapshot=beautify_snapshot,
            processing_standard_snapshots=standard_snapshots,
            routing_mode=routing_mode,
            completion_profile_id=completion_profile_id,
            completion_profile_snapshot=completion_snapshot,
            completed_filter_profile_id=completed_filter_profile_id,
            completed_filter_profile_snapshot=completed_filter_snapshot,
            non_completed_filter_profile_id=non_completed_filter_profile_id,
            non_completed_filter_profile_snapshot=non_completed_filter_snapshot,
            routing_policy_json=routing_policy,
            filter_enabled=payload.filter_enabled,
            beautify_enabled=payload.beautify_enabled,
            similarity_enabled=payload.similarity_enabled,
            similarity_profile_id=payload.similarity_profile,
            unmatched_standard_policy=payload.unmatched_standard_policy,
            enhance_level=payload.enhance_level,
            max_selected=payload.max_selected or len(payload.files),
            callback_url=str(payload.callback_url) if payload.callback_url else None,
            expires_at=now + timedelta(hours=self.settings.upload_batch_expiry_hours),
        )
        response_items: list[UploadBatchItemResponse] = []
        for file in payload.files:
            request = PresignedUploadRequest(**file.model_dump())
            try:
                validate_upload_request(request, self.settings)
            except InvalidUploadRequest as exc:
                raise InvalidUploadBatch(f"{file.filename}: {exc.message}") from exc
            object_key = build_upload_object_key(file.filename, file.content_type)
            item = UploadBatchItem(
                id=build_upload_batch_item_id(),
                filename=file.filename,
                content_type=file.content_type,
                file_size=file.file_size,
                object_key=object_key,
                status="registered",
            )
            batch.items.append(item)
            response_items.append(
                UploadBatchItemResponse(
                    id=item.id,
                    filename=item.filename,
                    content_type=item.content_type,
                    file_size=item.file_size,
                    object_key=item.object_key,
                    upload_url="",
                )
            )
        semaphore = asyncio.Semaphore(20)

        async def sign(item: UploadBatchItemResponse) -> None:
            async with semaphore:
                item.upload_url = await self.storage.presign_upload(
                    item.object_key,
                    item.content_type,
                    self.settings.upload_batch_presign_expires_seconds,
                )

        await asyncio.gather(*(sign(item) for item in response_items))
        await self.repository.create(batch)
        return CreateUploadBatchResponse(
            batch_id=batch.id,
            status=batch.status,
            expires_at=batch.expires_at,
            items=response_items,
        )

    async def complete(
        self, batch_id: str, payload: CompleteUploadBatchRequest
    ) -> CompleteUploadBatchResponse:
        batch = await self.repository.get_for_update(batch_id)
        if batch is None:
            raise UploadBatchNotFound("上传批次不存在")
        if batch.status == "completed" and batch.job_id:
            return CompleteUploadBatchResponse(
                batch_id=batch.id,
                job_id=batch.job_id,
                status=JobStatus.QUEUED.value,
                total=sum(item.status == "committed" for item in batch.items),
            )
        if batch.expires_at <= datetime.now(UTC):
            raise InvalidUploadBatch("上传批次已过期，请重新选择图片")

        requested_ids = set(payload.item_ids)
        items = [item for item in batch.items if item.id in requested_ids]
        if len(items) != len(requested_ids):
            raise InvalidUploadBatch("上传批次包含无效图片标识")
        semaphore = asyncio.Semaphore(20)

        async def validate_item(item: UploadBatchItem) -> None:
            try:
                async with semaphore:
                    object_size = await self.storage.get_size(item.object_key)
            except Exception as exc:
                raise InvalidUploadBatch(f"{item.filename} 尚未上传完成") from exc
            if object_size != item.file_size:
                raise InvalidUploadBatch(f"{item.filename} 上传大小不一致")

        await asyncio.gather(*(validate_item(item) for item in items))
        for item in items:
            item.status = "committed"
        for item in batch.items:
            if item.id not in requested_ids:
                item.status = "abandoned"

        job = ImageJob(
            id=build_job_id(),
            status=JobStatus.QUEUED.value,
            filter_profile_id=batch.filter_profile_id,
            beautify_profile_id=batch.beautify_profile_id,
            filter_profile_snapshot=batch.filter_profile_snapshot,
            beautify_profile_snapshot=batch.beautify_profile_snapshot,
            processing_standard_snapshots=batch.processing_standard_snapshots,
            routing_mode=batch.routing_mode,
            completion_profile_id=batch.completion_profile_id,
            completion_profile_snapshot=batch.completion_profile_snapshot,
            completed_filter_profile_id=batch.completed_filter_profile_id,
            completed_filter_profile_snapshot=batch.completed_filter_profile_snapshot,
            non_completed_filter_profile_id=batch.non_completed_filter_profile_id,
            non_completed_filter_profile_snapshot=batch.non_completed_filter_profile_snapshot,
            routing_policy_json=batch.routing_policy_json,
            filter_enabled=batch.filter_enabled,
            beautify_enabled=batch.beautify_enabled,
            similarity_enabled=batch.similarity_enabled,
            similarity_profile_id=batch.similarity_profile_id,
            unmatched_standard_policy=batch.unmatched_standard_policy,
            ai_tagging_model=(
                self.settings.ai_tagging_model if self.settings.ai_tagging_enabled else None
            ),
            enhance_level=batch.enhance_level,
            max_selected=min(batch.max_selected, len(items)),
            total_count=len(items),
            processed_count=0,
            selected_count=0,
            rejected_count=0,
            not_selected_count=0,
            dispatch_cursor=0,
            callback_url=batch.callback_url,
        )
        self.session.add(job)
        self.session.add_all(
            [
                ImageItem(
                    id=build_image_id(),
                    job_id=job.id,
                    object_key=item.object_key,
                    original_filename=item.filename,
                    status="queued",
                )
                for item in items
            ]
        )
        batch.status = "completed"
        batch.job_id = job.id
        batch.completed_at = datetime.now(UTC)
        await self.session.commit()
        JobDispatchTaskPublisher().publish(job.id)
        return CompleteUploadBatchResponse(
            batch_id=batch.id,
            job_id=job.id,
            status=job.status,
            total=job.total_count,
        )


def get_upload_batch_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UploadBatchService:
    return UploadBatchService(session, load_ai_model_settings(settings))
