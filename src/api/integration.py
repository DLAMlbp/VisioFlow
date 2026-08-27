import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import ValidationError

from src.api.jobs import JobServiceDep
from src.api.uploads import SettingsDep, StorageDep
from src.core.exceptions import InvalidUploadRequest
from src.schemas.jobs import (
    CreateImageJobRequest,
    CreateImageJobResponse,
    ImageJobProgressResponse,
    ImageJobResultsResponse,
)
from src.schemas.uploads import PresignedUploadRequest
from src.services.jobs.service import InvalidJobRequest, JobNotFound
from src.services.storage.keys import build_upload_object_key, validate_upload_request

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/jobs", response_model=CreateImageJobResponse, status_code=status.HTTP_201_CREATED)
async def create_integration_job(
    files: Annotated[list[UploadFile], File(description="待处理的装修现场图片，1 至 50 张")],
    service: JobServiceDep,
    settings: SettingsDep,
    storage: StorageDep,
    filter_profile: Annotated[str, Form()] = "renovation_submission_v1",
    beautify_profile: Annotated[str, Form()] = "renovation_natural_v1",
    similarity_profile: Annotated[str, Form()] = "library_similarity_v2",
    enhance_level: Annotated[int, Form(ge=0, le=2)] = 1,
    max_selected: Annotated[int, Form(ge=1)] = 10,
    callback_url: Annotated[str | None, Form()] = None,
) -> CreateImageJobResponse:
    """上传图片并创建异步处理任务，供第三方平台直接调用。"""
    if len(files) > settings.max_images_per_job:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"单个 Job 最多支持 {settings.max_images_per_job} 张图片",
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
            filter_profile=filter_profile,
            beautify_profile=beautify_profile,
            similarity_profile=similarity_profile,
            enhance_level=enhance_level,
            max_selected=max_selected,
            images=[{"object_key": object_key} for object_key in image_keys],
            callback_url=callback_url or None,
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


@router.get("/jobs/{job_id}/results", response_model=ImageJobResultsResponse)
async def get_integration_job_results(
    job_id: str,
    service: JobServiceDep,
) -> ImageJobResultsResponse:
    try:
        return await service.get_results(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc


async def _delete_uploaded_objects(storage: StorageDep, object_keys: list[str]) -> None:
    for object_key in object_keys:
        try:
            await storage.delete(object_key)
        except Exception:
            logger.warning("Failed to remove unowned upload: %s", object_key, exc_info=True)
