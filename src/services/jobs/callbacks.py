import asyncio
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from src.core.config import Settings
from src.repositories.jobs import CallbackJob, ImageJobRepository
from src.schemas.jobs import ImageJobResultItemResponse
from src.services.jobs.service import ImageJobService
from src.services.storage.interfaces import StorageProvider


class CallbackDeliveryError(RuntimeError):
    pass


class CallbackImageResult(ImageJobResultItemResponse):
    original_url: str | None = None
    enhanced_url: str | None = None


class ImageJobCallbackPayload(BaseModel):
    event_id: str
    event: str = "image.job.finished"
    job_id: str
    status: str
    completed_at: datetime
    total: int
    selected: int
    rejected: int
    not_selected: int = 0
    result_total: int = 0
    download_expires_in: int = Field(ge=1)
    images: list[CallbackImageResult]
    error_message: str | None = None


async def build_job_callback_payload(
    callback_job: CallbackJob,
    repository: ImageJobRepository,
    settings: Settings,
    storage: StorageProvider,
) -> ImageJobCallbackPayload:
    result = await ImageJobService(repository, settings).get_results(
        callback_job.id,
        limit=max(1, settings.max_images_per_job),
    )
    images: list[CallbackImageResult] = []
    for image in result.images:
        original_url = None
        enhanced_url = None
        if not image.files_expired:
            original_url = await _safe_presign(
                storage, image.original_object_key, settings.s3_presign_expires_seconds
            )
            if image.enhanced_object_key:
                enhanced_url = await _safe_presign(
                    storage, image.enhanced_object_key, settings.s3_presign_expires_seconds
                )
        images.append(
            CallbackImageResult(
                **image.model_dump(),
                original_url=original_url,
                enhanced_url=enhanced_url,
            )
        )

    return ImageJobCallbackPayload(
        event_id=f"{callback_job.id}:{callback_job.completed_at.isoformat()}",
        job_id=callback_job.id,
        status=callback_job.status,
        completed_at=callback_job.completed_at,
        total=result.total,
        selected=result.selected,
        rejected=result.rejected,
        not_selected=result.not_selected,
        result_total=result.result_total,
        download_expires_in=settings.s3_presign_expires_seconds,
        images=images,
        error_message=_job_error_message(callback_job.status),
    )


async def post_job_callback(
    callback_url: str,
    payload: ImageJobCallbackPayload,
    *,
    timeout_seconds: int,
) -> None:
    parsed = urlsplit(callback_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CallbackDeliveryError("callback_url 必须是有效的 http/https URL")
    body = payload.model_dump_json(exclude_none=False).encode("utf-8")
    await asyncio.to_thread(
        _post_json,
        callback_url,
        body,
        payload.event,
        payload.event_id,
        max(1, timeout_seconds),
    )


def callback_destination(callback_url: str) -> str:
    parsed = urlsplit(callback_url)
    return parsed.hostname or "unknown-host"


def _post_json(
    callback_url: str,
    body: bytes,
    event: str,
    event_id: str,
    timeout_seconds: int,
) -> None:
    request = urllib.request.Request(
        callback_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "image-intelligence-service/1.0",
            "X-Callback-Event": event,
            "X-Callback-Id": event_id,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        raise CallbackDeliveryError(f"回调返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CallbackDeliveryError(f"回调连接失败：{exc.reason if hasattr(exc, 'reason') else exc}") from exc
    if status < 200 or status >= 300:
        raise CallbackDeliveryError(f"回调返回 HTTP {status}")


async def _safe_presign(
    storage: StorageProvider,
    object_key: str,
    expires_seconds: int,
) -> str | None:
    try:
        return await storage.presign_download(object_key, expires_seconds)
    except Exception:  # noqa: BLE001 - a missing signed URL must not block the whole callback
        return None


def _job_error_message(status: str) -> str | None:
    if status == "failed":
        return "任务处理失败"
    if status == "partial_failed":
        return "部分图片处理失败，成功结果仍可使用"
    if status == "cancelled":
        return "任务已取消"
    return None
