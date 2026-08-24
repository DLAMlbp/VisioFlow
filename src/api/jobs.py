from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.schemas.jobs import (
    CreateImageJobRequest,
    CreateImageJobResponse,
    ImageJobHistoryResponse,
    ImageJobProgressResponse,
    ImageJobResultsResponse,
)
from src.services.jobs.service import (
    ImageJobService,
    InvalidJobRequest,
    JobNotFound,
    get_job_service,
)

router = APIRouter()
JobServiceDep = Annotated[ImageJobService, Depends(get_job_service)]


@router.post("", response_model=CreateImageJobResponse, status_code=status.HTTP_201_CREATED)
async def create_image_job(
    payload: CreateImageJobRequest,
    service: JobServiceDep,
) -> CreateImageJobResponse:
    try:
        return await service.create_job(payload)
    except InvalidJobRequest as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc


@router.get("", response_model=ImageJobHistoryResponse)
async def list_image_job_history(
    service: JobServiceDep,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ImageJobHistoryResponse:
    return await service.list_history(limit, offset)


@router.get("/{job_id}", response_model=ImageJobProgressResponse)
async def get_image_job(
    job_id: str,
    service: JobServiceDep,
) -> ImageJobProgressResponse:
    try:
        return await service.get_progress(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc


@router.get("/{job_id}/results", response_model=ImageJobResultsResponse)
async def get_image_job_results(
    job_id: str,
    service: JobServiceDep,
) -> ImageJobResultsResponse:
    try:
        return await service.get_results(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc

