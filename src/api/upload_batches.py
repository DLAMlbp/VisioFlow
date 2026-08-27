from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from src.schemas.upload_batches import (
    CompleteUploadBatchRequest,
    CompleteUploadBatchResponse,
    CreateUploadBatchRequest,
    CreateUploadBatchResponse,
)
from src.services.upload_batches import (
    InvalidUploadBatch,
    UploadBatchNotFound,
    UploadBatchService,
    get_upload_batch_service,
)

router = APIRouter()
ServiceDep = Annotated[UploadBatchService, Depends(get_upload_batch_service)]


@router.post("", response_model=CreateUploadBatchResponse, status_code=status.HTTP_201_CREATED)
async def create_upload_batch(
    payload: CreateUploadBatchRequest, service: ServiceDep
) -> CreateUploadBatchResponse:
    try:
        return await service.create(payload)
    except InvalidUploadBatch as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc


@router.post("/{batch_id}/complete", response_model=CompleteUploadBatchResponse)
async def complete_upload_batch(
    batch_id: str, payload: CompleteUploadBatchRequest, service: ServiceDep
) -> CompleteUploadBatchResponse:
    try:
        return await service.complete(batch_id, payload)
    except UploadBatchNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    except InvalidUploadBatch as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
