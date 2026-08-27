from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from src.schemas.library import (
    LibraryAssetCreate,
    LibraryAssetListResponse,
    LibraryAssetResponse,
    LibraryAssetUpdate,
    LibraryTagNodeCreate,
    LibraryTagNodeResponse,
    LibraryTagNodeUpdate,
    TagReviewDecisionRequest,
    TagReviewResponse,
)
from src.services.library import (
    InvalidLibraryRequest,
    LibraryNotFound,
    LibraryService,
    get_library_service,
)

router = APIRouter()
review_router = APIRouter()
LibraryServiceDep = Annotated[LibraryService, Depends(get_library_service)]


@router.get("/tag-tree", response_model=list[LibraryTagNodeResponse])
async def get_tag_tree(service: LibraryServiceDep) -> list[LibraryTagNodeResponse]:
    return await service.get_tag_tree()


@router.post(
    "/tag-nodes", response_model=LibraryTagNodeResponse, status_code=status.HTTP_201_CREATED
)
async def create_tag_node(
    payload: LibraryTagNodeCreate, service: LibraryServiceDep
) -> LibraryTagNodeResponse:
    try:
        return await service.create_tag_node(payload)
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


@router.patch("/tag-nodes/{node_id}", response_model=LibraryTagNodeResponse)
async def update_tag_node(
    node_id: str, payload: LibraryTagNodeUpdate, service: LibraryServiceDep
) -> LibraryTagNodeResponse:
    try:
        return await service.update_tag_node(node_id, payload)
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


@router.delete("/tag-nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag_node(node_id: str, service: LibraryServiceDep) -> Response:
    try:
        await service.delete_tag_node(node_id)
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
    leaf_tag_node_id: str | None = None,
    asset_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LibraryAssetListResponse:
    return await service.list_assets(
        leaf_tag_node_id=leaf_tag_node_id,
        status=asset_status,
        limit=limit,
        offset=offset,
    )


@router.patch("/assets/{asset_id}", response_model=LibraryAssetResponse)
async def update_library_asset(
    asset_id: str, payload: LibraryAssetUpdate, service: LibraryServiceDep
) -> LibraryAssetResponse:
    try:
        return await service.update_asset(asset_id, payload)
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


@review_router.get("/tag-reviews", response_model=list[TagReviewResponse])
async def list_tag_reviews(
    service: LibraryServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[TagReviewResponse]:
    return await service.list_reviews(limit, offset)


@review_router.post("/tag-reviews/{image_id}/decision", response_model=TagReviewResponse)
async def decide_tag_review(
    image_id: str, payload: TagReviewDecisionRequest, service: LibraryServiceDep
) -> TagReviewResponse:
    try:
        return await service.decide_review(image_id, payload)
    except (InvalidLibraryRequest, LibraryNotFound) as exc:
        raise _http_error(exc) from exc


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, LibraryNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error.message)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error.message)
