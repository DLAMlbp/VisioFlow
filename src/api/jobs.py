from collections.abc import Iterator
from tempfile import TemporaryFile
from typing import Annotated, BinaryIO
from zipfile import ZIP_STORED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

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
from src.services.storage.factory import get_storage_provider

router = APIRouter()
JobServiceDep = Annotated[ImageJobService, Depends(get_job_service)]


class SelectedImageArchiveRequest(BaseModel):
    image_ids: list[str] | None = Field(default=None, max_length=100)


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
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    decision: str | None = Query(default=None),
    completion_label: str | None = Query(
        default=None, pattern="^(completed|non_completed)$"
    ),
    review_required: bool | None = Query(default=None),
) -> ImageJobResultsResponse:
    try:
        completion_filters: dict[str, object] = {}
        if completion_label is not None:
            completion_filters["completion_label"] = completion_label
        if review_required is not None:
            completion_filters["review_required"] = review_required
        return await service.get_results(
            job_id,
            limit=limit,
            offset=offset,
            decision=decision,
            **completion_filters,
        )
    except JobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc


@router.post("/{job_id}/downloads/selected")
async def download_selected_images(
    job_id: str,
    payload: SelectedImageArchiveRequest,
    service: JobServiceDep,
) -> StreamingResponse:
    try:
        downloads = await service.get_selected_downloads(job_id, payload.image_ids)
    except JobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    if not downloads:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="没有可下载的美化图片")

    archive = TemporaryFile(mode="w+b")
    try:
        storage = get_storage_provider()
        with ZipFile(archive, mode="w", compression=ZIP_STORED) as zip_file:
            for download in downloads:
                zip_file.writestr(download.archive_filename, await storage.download(download.object_key))
        archive.seek(0)
    except Exception as exc:
        archive.close()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="部分美化图片暂时无法下载，请稍后重试",
        ) from exc

    filename = f"{job_id}_enhanced_images.zip"
    return StreamingResponse(
        _read_archive_chunks(archive),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=BackgroundTask(archive.close),
    )


def _read_archive_chunks(archive: BinaryIO, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    while chunk := archive.read(chunk_size):
        yield chunk


@router.post("/{job_id}/cancel", response_model=ImageJobProgressResponse)
async def cancel_image_job(job_id: str, service: JobServiceDep) -> ImageJobProgressResponse:
    try:
        return await service.cancel_job(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except InvalidJobRequest as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc


@router.post("/{job_id}/images/{image_id}/retry", response_model=ImageJobProgressResponse)
async def retry_failed_image(
    job_id: str, image_id: str, service: JobServiceDep
) -> ImageJobProgressResponse:
    try:
        return await service.retry_failed_image(job_id, image_id)
    except InvalidJobRequest as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
