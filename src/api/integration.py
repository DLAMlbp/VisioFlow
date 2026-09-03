import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile

from src.api.jobs import JobServiceDep
from src.api.uploads import SettingsDep, StorageDep
from src.core.exceptions import InvalidUploadRequest
from src.schemas.integration import IntegrationCreateResponse, IntegrationUrlJobRequest
from src.schemas.jobs import (
    CompletionFilterRoute,
    CreateImageJobRequest,
    CreateImageJobResponse,
    FilterRoutingPolicy,
    ImageJobProgressResponse,
    ImageJobResultItemResponse,
)
from src.schemas.uploads import PresignedUploadRequest
from src.services.integration_admission import (
    IntegrationAdmissionRejected,
    IntegrationAdmissionUnavailable,
    enforce_integration_admission,
)
from src.services.integration_urls import (
    IntegrationUrlDownloadError,
    delete_staged_integration_images,
    stage_integration_urls,
)
from src.services.jobs.service import InvalidJobRequest, JobNotFound
from src.services.storage.keys import build_upload_object_key, validate_upload_request

router = APIRouter()
partner_router = APIRouter()
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


@router.post(
    "/jobs",
    response_model=IntegrationCreateResponse | CreateImageJobResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/IntegrationUrlJobRequest"}
                }
            },
        }
    },
)
async def create_integration_job(
    request: Request,
    service: JobServiceDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> IntegrationCreateResponse | CreateImageJobResponse:
    """通过 URL JSON 创建任务，同时兼容原有 multipart 文件上传。"""
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            payload = IntegrationUrlJobRequest.model_validate(await request.json())
        except (ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        return await _create_url_job(payload, service=service, settings=settings, storage=storage)
    if "multipart/form-data" in content_type:
        form = await request.form()
        files = [
            value for value in form.getlist("files") if isinstance(value, StarletteUploadFile)
        ]
        callback_url = _required_form_string(form, "callback_url")
        return await create_file_integration_job(
            files=files,
            service=service,
            settings=settings,
            storage=storage,
            callback_url=callback_url,
            beautify_profile=_optional_form_string(form, "beautify_profile"),
            redaction_profile=_optional_form_string(form, "redaction_profile"),
            processing_standards=_optional_form_string(form, "processing_standards"),
            completion_profile=_optional_form_string(form, "completion_profile"),
            completed_filter_profile=_optional_form_string(form, "completed_filter_profile"),
            non_completed_filter_profile=_optional_form_string(
                form, "non_completed_filter_profile"
            ),
            insufficient_evidence_policy=_form_string(
                form, "insufficient_evidence_policy", "route_non_completed"
            ),
            low_confidence_policy=_form_string(
                form, "low_confidence_policy", "continue_with_review"
            ),
            filter_profile=_optional_form_string(form, "filter_profile"),
            filter_enabled=_form_bool(form, "filter_enabled", True),
            beautify_enabled=_form_bool(form, "beautify_enabled", True),
            watermark_processing_enabled=_form_bool(
                form, "watermark_processing_enabled", False
            ),
            similarity_enabled=_form_bool(form, "similarity_enabled", True),
            similarity_profile=_form_string(
                form, "similarity_profile", "library_similarity_v2"
            ),
            unmatched_standard_policy=_form_string(
                form, "unmatched_standard_policy", "reject"
            ),
            enhance_level=_form_int(form, "enhance_level", 1),
            max_selected=_form_int(form, "max_selected", 10),
        )
    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail="Content-Type 必须是 application/json 或 multipart/form-data",
    )


@partner_router.post(
    "/api/app/image/filter-requests",
    response_model=IntegrationCreateResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_partner_filter_request(
    payload: IntegrationUrlJobRequest,
    service: JobServiceDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> IntegrationCreateResponse:
    """兼容客户原始 submitPath 的 URL 图片任务入口。"""
    return await _create_url_job(payload, service=service, settings=settings, storage=storage)


@router.post("/file-jobs", response_model=CreateImageJobResponse, status_code=status.HTTP_201_CREATED)
async def create_file_integration_job(
    files: Annotated[list[UploadFile], File(description="待处理图片，1 至 50 张")],
    service: JobServiceDep,
    settings: SettingsDep,
    storage: StorageDep,
    callback_url: Annotated[str, Form(min_length=1)],
    beautify_profile: Annotated[str | None, Form(min_length=1)] = None,
    redaction_profile: Annotated[str | None, Form(min_length=1)] = None,
    processing_standards: Annotated[
        str | None, Form(description="兼容字段；新任务自动使用全部启用标准")
    ] = None,
    completion_profile: Annotated[str | None, Form(min_length=1)] = None,
    completed_filter_profile: Annotated[str | None, Form(min_length=1)] = None,
    non_completed_filter_profile: Annotated[str | None, Form(min_length=1)] = None,
    insufficient_evidence_policy: Annotated[
        str, Form(pattern="^(reject|route_non_completed)$")
    ] = "route_non_completed",
    low_confidence_policy: Annotated[
        str, Form(pattern="^(continue_with_review|reject)$")
    ] = "continue_with_review",
    filter_profile: Annotated[str | None, Form()] = None,
    filter_enabled: Annotated[bool, Form()] = True,
    beautify_enabled: Annotated[bool, Form()] = True,
    watermark_processing_enabled: Annotated[bool, Form()] = False,
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
    _enforce_integration_admission(settings, len(files))
    route_values = (
        completion_profile,
        completed_filter_profile,
        non_completed_filter_profile,
    )
    standard_ids = [
        value.strip()
        for value in (processing_standards or "").split(",")
        if value.strip()
    ]
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
            filter_route=(
                CompletionFilterRoute(
                    completion_profile=completion_profile,
                    completed_filter_profile=completed_filter_profile,
                    non_completed_filter_profile=non_completed_filter_profile,
                    policy=FilterRoutingPolicy(
                        insufficient_evidence_policy=insufficient_evidence_policy,
                        low_confidence_policy=low_confidence_policy,
                    ),
                )
                if all(route_values)
                else None
            ),
            processing_standards=standard_ids,
            filter_profile=filter_profile,
            beautify_profile=beautify_profile,
            redaction_profile=redaction_profile,
            filter_enabled=filter_enabled,
            beautify_enabled=beautify_enabled,
            watermark_processing_enabled=watermark_processing_enabled,
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


async def _create_url_job(
    payload: IntegrationUrlJobRequest,
    *,
    service: JobServiceDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> IntegrationCreateResponse:
    if len(payload.images) > settings.integration_max_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"集成接口单次最多支持 {settings.integration_max_files} 张图片",
        )
    _enforce_integration_admission(settings, len(payload.images))
    staged = []
    try:
        staged = await stage_integration_urls(payload.images, storage=storage, settings=settings)
        request = CreateImageJobRequest(
            filter_route=CompletionFilterRoute(
                completion_profile=(
                    payload.completion_profile or settings.integration_completion_profile
                ),
                completed_filter_profile=(
                    payload.completed_filter_profile
                    or settings.integration_completed_filter_profile
                ),
                non_completed_filter_profile=(
                    payload.non_completed_filter_profile
                    or settings.integration_non_completed_filter_profile
                ),
                policy=FilterRoutingPolicy(),
            ),
            beautify_profile=(
                payload.beautify_profile or settings.integration_beautify_profile
            ),
            redaction_profile=payload.redaction_profile,
            watermark_processing_enabled=payload.watermark_processing_enabled,
            similarity_profile=payload.similarity_profile,
            enhance_level=payload.enhance_level,
            max_selected=payload.max_selected,
            images=[
                {
                    "object_key": image.object_key,
                    "client_object_key": image.client_object_key,
                }
                for image in staged
            ],
            callback_url=payload.notify_url,
            callback_contract="customer_v1",
        )
        created = await service.create_job(request)
    except (IntegrationUrlDownloadError, InvalidJobRequest, ValidationError) as exc:
        await delete_staged_integration_images(storage, staged)
        detail = exc.message if isinstance(exc, InvalidJobRequest) else str(exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc
    except Exception:
        await delete_staged_integration_images(storage, staged)
        raise
    return IntegrationCreateResponse(
        job_id=created.job_id,
        task_id=created.job_id,
        status=created.status.value,
        total=created.total,
    )


def _enforce_integration_admission(settings, image_count: int) -> None:
    try:
        enforce_integration_admission(settings, image_count)
    except IntegrationAdmissionRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={
                "Retry-After": str(exc.retry_after_seconds),
                "X-Pipeline-Queue-Depth": str(exc.queue_depth),
            },
        ) from exc
    except IntegrationAdmissionUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="任务容量检查暂时不可用，请稍后重试",
            headers={"Retry-After": "30"},
        ) from exc


def _required_form_string(form: FormData, name: str) -> str:
    value = _optional_form_string(form, name)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"缺少必填字段：{name}",
        )
    return value


def _optional_form_string(form: FormData, name: str) -> str | None:
    value = form.get(name)
    if value is None or isinstance(value, StarletteUploadFile):
        return None
    text = str(value).strip()
    return text or None


def _form_string(form: FormData, name: str, default: str) -> str:
    return _optional_form_string(form, name) or default


def _form_int(form: FormData, name: str, default: int) -> int:
    value = _optional_form_string(form, name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"字段 {name} 必须是整数",
        ) from exc


def _form_bool(form: FormData, name: str, default: bool) -> bool:
    value = _optional_form_string(form, name)
    if value is None:
        return default
    normalized = value.lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"字段 {name} 必须是布尔值",
    )


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
