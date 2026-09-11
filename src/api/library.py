from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from src.schemas.library import (
    LibraryAssetBulkDeleteRequest,
    LibraryAssetBulkDeleteResponse,
    LibraryAssetCreate,
    LibraryAssetGroupBulkDeleteResponse,
    LibraryAssetGroupCreate,
    LibraryAssetGroupResponse,
    LibraryAssetGroupUpdate,
    LibraryAssetListResponse,
    LibraryAssetResponse,
    LibraryAssetUpdate,
    LibraryFailedAssetReindexResponse,
)
from src.services.library import (
    InvalidLibraryRequest,
    LibraryNotFound,
    LibraryService,
    get_library_service,
)

router = APIRouter()
LibraryServiceDep = Annotated[LibraryService, Depends(get_library_service)]


@router.get("/groups", response_model=list[LibraryAssetGroupResponse])
async def get_groups(service: LibraryServiceDep) -> list[LibraryAssetGroupResponse]:
    return await service.get_groups()


@router.post(
    "/groups", response_model=LibraryAssetGroupResponse, status_code=status.HTTP_201_CREATED
)
async def create_group(
    payload: LibraryAssetGroupCreate, service: LibraryServiceDep
) -> LibraryAssetGroupResponse:
    try:
        return await service.create_group(payload)
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


@router.delete("/groups", response_model=LibraryAssetGroupBulkDeleteResponse)
async def delete_all_groups(service: LibraryServiceDep) -> LibraryAssetGroupBulkDeleteResponse:
    try:
        return await service.delete_all_groups()
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


@router.patch("/groups/{group_id}", response_model=LibraryAssetGroupResponse)
async def update_group(
    group_id: str, payload: LibraryAssetGroupUpdate, service: LibraryServiceDep
) -> LibraryAssetGroupResponse:
    try:
        return await service.update_group(group_id, payload)
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: str, service: LibraryServiceDep) -> Response:
    try:
        await service.delete_group(group_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


@router.post("/assets", response_model=LibraryAssetResponse, status_code=status.HTTP_201_CREATED)
async def create_library_asset(
    payload: LibraryAssetCreate, service: LibraryServiceDep
) -> LibraryAssetResponse:
    try:
        return await service.create_asset(payload)
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


@router.get("/assets", response_model=LibraryAssetListResponse)
async def list_library_assets(
    service: LibraryServiceDep,
    group_id: str | None = None,
    asset_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LibraryAssetListResponse:
    return await service.list_assets(
        group_id=group_id,
        status=asset_status,
        limit=limit,
        offset=offset,
    )


@router.post("/assets/bulk-delete", response_model=LibraryAssetBulkDeleteResponse)
async def bulk_delete_library_assets(
    payload: LibraryAssetBulkDeleteRequest, service: LibraryServiceDep
) -> LibraryAssetBulkDeleteResponse:
    try:
        return await service.bulk_delete_assets(payload)
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


@router.patch("/assets/{asset_id}", response_model=LibraryAssetResponse)
async def update_library_asset(
    asset_id: str, payload: LibraryAssetUpdate, service: LibraryServiceDep
) -> LibraryAssetResponse:
    try:
        return await service.update_asset(asset_id, payload)
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_library_asset(asset_id: str, service: LibraryServiceDep) -> Response:
    try:
        await service.delete_asset(asset_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


@router.post("/assets/{asset_id}/reindex", response_model=LibraryAssetResponse)
async def reindex_library_asset(
    asset_id: str, service: LibraryServiceDep
) -> LibraryAssetResponse:
    try:
        return await service.reindex_asset(asset_id)
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


@router.post("/assets/reindex-failed", response_model=LibraryFailedAssetReindexResponse)
async def reindex_failed_library_assets(
    service: LibraryServiceDep,
    group_id: str | None = None,
) -> LibraryFailedAssetReindexResponse:
    try:
        return await service.reindex_failed_assets(group_id=group_id)
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, LibraryNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error.message)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error.message)
