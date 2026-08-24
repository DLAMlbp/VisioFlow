from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from src.core.config import Settings, get_settings
from src.core.exceptions import InvalidUploadRequest
from src.schemas.uploads import (
    PresignedDownloadRequest,
    PresignedDownloadResponse,
    PresignedUploadRequest,
    PresignedUploadResponse,
)
from src.services.storage.factory import get_storage_provider
from src.services.storage.interfaces import StorageProvider
from src.services.storage.keys import (
    build_upload_object_key,
    validate_object_key,
    validate_upload_request,
)

router = APIRouter()
SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[StorageProvider, Depends(get_storage_provider)]


@router.post("/presign", response_model=PresignedUploadResponse)
async def presign_upload(
    payload: PresignedUploadRequest,
    settings: SettingsDep,
    storage: StorageDep,
) -> PresignedUploadResponse:
    try:
        validate_upload_request(payload, settings=settings)
        object_key = build_upload_object_key(payload.filename, payload.content_type)
        upload_url = await storage.presign_upload(
            object_key=object_key,
            content_type=payload.content_type,
            expires_seconds=settings.s3_presign_expires_seconds,
        )
    except InvalidUploadRequest as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc

    return PresignedUploadResponse(object_key=object_key, upload_url=upload_url)


@router.post("/presign-download", response_model=PresignedDownloadResponse)
async def presign_download(
    payload: PresignedDownloadRequest,
    settings: SettingsDep,
    storage: StorageDep,
) -> PresignedDownloadResponse:
    try:
        validate_object_key(payload.object_key)
        download_url = await storage.presign_download(
            object_key=payload.object_key,
            expires_seconds=settings.s3_presign_expires_seconds,
        )
    except InvalidUploadRequest as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc

    return PresignedDownloadResponse(object_key=payload.object_key, download_url=download_url)
