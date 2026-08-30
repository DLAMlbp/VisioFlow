import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field, ValidationError

from src.api.jobs import JobServiceDep
from src.api.uploads import SettingsDep, StorageDep
from src.core.exceptions import InvalidUploadRequest
from src.schemas.jobs import (
    CreateImageJobRequest,
    CreateImageJobResponse,
    ImageJobProgressResponse,
    ImageJobResultItemResponse,
)
from src.schemas.uploads import PresignedUploadRequest
from src.services.jobs.service import InvalidJobRequest, JobNotFound
from src.services.storage.keys import build_upload_object_key, validate_upload_request

router = APIRouter()
logger = logging.getLogger(__name__)


class IntegrationImageResultResponse(ImageJobResultItemResponse):
    original_url: str | None = None
    enhanced_url: str | None = None


class IntegrationJobResultsResponse(BaseModel):
    job_id: str
    total: int
    selected: int
    rejected: int
    not_selected: int = 0
    result_total: int = 0
    limit: int = 50
    offset: int = 0
    download_expires_in: int = Field(ge=1)
    images: list[IntegrationImageResultResponse]


@router.post("/jobs", response_model=CreateImageJobResponse, status_code=status.HTTP_201_CREATED)
async def create_integration_job(
    files: Annotated[list[UploadFile], File(description="待处理图片，1 至 50 张")],
    service: JobServiceDep,
    settings: SettingsDep,
    storage: StorageDep,
    callback_url: Annotated[str, Form(min_length=1)],
    beautify_profile: Annotated[str | None, Form(min_length=1)] = None,
    processing_standards: Annotated[
        str | None, Form(description="逗号分隔的两套互斥且完整覆盖的过滤标准 ID")
    ] = None,
    filter_profile: Annotated[str | None, Form()] = None,
    filter_enabled: Annotated[bool, Form()] = True,
    beautify_enabled: Annotated[bool, Form()] = True,
    similarity_enabled: Annotated[bool, Form()] = True,
    similarity_profile: Annotated[str, Form()] = "library_similarity_v2",
    unmatched_standard_policy: Annotated[str, Form(pattern="^reject$")] = "reject",
    enhance_level: Annotated[int, Form(ge=0, le=2)] = 1,
    max_selected: Annotated[int, Form(ge=1)] = 10,
) -> CreateImageJobResponse:
    """上传图片并创建异步处理任务，供第三方平台直接调用。"""
    if len(files) > settings.integration_max_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"集成接口单次最多支持 {settings.integration_max_files} 张图片",
        )

    uploaded_keys: list[str] = []
    try:
        image_keys: list[str] = []
        for file in files:
            data = await file.read()
            upload_request = PresignedUploadRequest(
                filename=file.filename or "upload",
                content_type=file.content_type or "",
                file_size=len(data),
            )
            validate_upload_request(upload_request, settings)
            object_key = build_upload_object_key(upload_request.filename, upload_request.content_type)
            await storage.upload(object_key, data, upload_request.content_type)
            uploaded_keys.append(object_key)
            image_keys.append(object_key)

        payload = CreateImageJobRequest(
            processing_standards=[
                value.strip()
                for value in (processing_standards or "").split(",")
                if value.strip()
            ],
            filter_profile=filter_profile,
            beautify_profile=beautify_profile,
            filter_enabled=filter_enabled,
            beautify_enabled=beautify_enabled,
            similarity_enabled=similarity_enabled,
            similarity_profile=similarity_profile,
            unmatched_standard_policy=unmatched_standard_policy,
            enhance_level=enhance_level,
            max_selected=max_selected,
            images=[{"object_key": object_key} for object_key in image_keys],
            callback_url=callback_url,
        )
        return await service.create_job(payload)
    except (InvalidUploadRequest, InvalidJobRequest, ValidationError) as exc:
        await _delete_uploaded_objects(storage, uploaded_keys)
        detail = exc.message if isinstance(exc, (InvalidUploadRequest, InvalidJobRequest)) else str(exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
    except Exception:
        await _delete_uploaded_objects(storage, uploaded_keys)
        raise


@router.get("/jobs/{job_id}", response_model=ImageJobProgressResponse)
async def get_integration_job(job_id: str, service: JobServiceDep) -> ImageJobProgressResponse:
    try:
        return await service.get_progress(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc


@router.get("/jobs/{job_id}/results", response_model=IntegrationJobResultsResponse)
async def get_integration_job_results(
    job_id: str,
    service: JobServiceDep,
    settings: SettingsDep,
    storage: StorageDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    decision: Annotated[str | None, Query()] = None,
) -> IntegrationJobResultsResponse:
    try:
        result = await service.get_results(job_id, limit=limit, offset=offset, decision=decision)
    except JobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc

    images: list[IntegrationImageResultResponse] = []
    for image in result.images:
        original_url = None
        enhanced_url = None
        if not image.files_expired:
            original_url = await _safe_presign_download(storage, image.original_object_key, settings)
            if image.enhanced_object_key:
                enhanced_url = await _safe_presign_download(storage, image.enhanced_object_key, settings)
        images.append(
            IntegrationImageResultResponse(
                **image.model_dump(),
                original_url=original_url,
                enhanced_url=enhanced_url,
            )
        )
    return IntegrationJobResultsResponse(
        **result.model_dump(exclude={"images"}),
        download_expires_in=settings.s3_presign_expires_seconds,
        images=images,
    )


async def _delete_uploaded_objects(storage: StorageDep, object_keys: list[str]) -> None:
    for object_key in object_keys:
        try:
            await storage.delete(object_key)
        except Exception:
            logger.warning("Failed to remove unowned upload: %s", object_key, exc_info=True)


async def _safe_presign_download(storage: StorageDep, object_key: str, settings: SettingsDep) -> str | None:
    try:
        return await storage.presign_download(object_key, settings.s3_presign_expires_seconds)
    except Exception:
        logger.warning("Failed to create integration download URL: %s", object_key, exc_info=True)
        return None
