from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

from src.core.config import Settings
from src.schemas.integration import IntegrationUrlImage
from src.schemas.uploads import PresignedUploadRequest
from src.services.images.metadata import (
    detect_image_content_type,
    pillow_format_to_content_type,
)
from src.services.storage.interfaces import StorageProvider
from src.services.storage.keys import (
    CONTENT_TYPE_EXTENSIONS,
    build_upload_object_key,
    validate_upload_request,
)

logger = logging.getLogger(__name__)


class IntegrationUrlDownloadError(ValueError):
    pass


@dataclass(frozen=True)
class StagedIntegrationImage:
    object_key: str
    client_object_key: str


async def stage_integration_urls(
    images: list[IntegrationUrlImage],
    *,
    storage: StorageProvider,
    settings: Settings,
) -> list[StagedIntegrationImage]:
    semaphore = asyncio.Semaphore(max(1, settings.integration_url_download_concurrency))

    async def stage_one(image: IntegrationUrlImage) -> StagedIntegrationImage:
        async with semaphore:
            try:
                data = await asyncio.to_thread(
                    _download_image,
                    str(image.image_url),
                    max_bytes=settings.max_image_size_mb * 1024 * 1024,
                    timeout_seconds=max(1, settings.integration_url_download_timeout_seconds),
                )
                content_type = _validated_image_content_type(
                    data,
                    settings.allowed_image_content_types,
                )
            except IntegrationUrlDownloadError as exc:
                raise IntegrationUrlDownloadError(
                    f"图片 URL 下载失败（objectKey={image.object_key}）：{exc}"
                ) from exc
            filename = f"remote-image{CONTENT_TYPE_EXTENSIONS[content_type]}"
            validate_upload_request(
                PresignedUploadRequest(
                    filename=filename,
                    content_type=content_type,
                    file_size=len(data),
                ),
                settings,
            )
            object_key = build_upload_object_key(filename, content_type)
            await storage.upload(object_key, data, content_type)
            return StagedIntegrationImage(
                object_key=object_key,
                client_object_key=image.object_key,
            )

    outcomes = await asyncio.gather(*(stage_one(image) for image in images), return_exceptions=True)
    staged = [outcome for outcome in outcomes if isinstance(outcome, StagedIntegrationImage)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    if failures:
        await _delete_staged(storage, staged)
        first = failures[0]
        if isinstance(first, IntegrationUrlDownloadError):
            raise first
        raise IntegrationUrlDownloadError(f"图片 URL 下载失败：{first}") from first
    return staged


async def delete_staged_integration_images(
    storage: StorageProvider,
    staged: list[StagedIntegrationImage],
) -> None:
    await _delete_staged(storage, staged)


async def _delete_staged(
    storage: StorageProvider,
    staged: list[StagedIntegrationImage],
) -> None:
    for image in staged:
        try:
            await storage.delete(image.object_key)
        except Exception:
            logger.warning(
                "Failed to remove staged integration URL image: %s",
                image.object_key,
                exc_info=True,
            )


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_image(
    image_url: str,
    *,
    max_bytes: int,
    timeout_seconds: int,
) -> bytes:
    _validate_public_http_url(image_url)
    request = urllib.request.Request(
        image_url,
        headers={
            "Accept": "image/jpeg,image/png,image/webp",
            "User-Agent": "image-intelligence-service/1.0",
        },
    )
    opener = urllib.request.build_opener(_PublicRedirectHandler())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise IntegrationUrlDownloadError("图片大小超过服务限制")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(64 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise IntegrationUrlDownloadError("图片大小超过服务限制")
    except IntegrationUrlDownloadError:
        raise
    except urllib.error.HTTPError as exc:
        raise IntegrationUrlDownloadError(f"图片 URL 返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise IntegrationUrlDownloadError(f"图片 URL 无法下载：{exc}") from exc
    return b"".join(chunks)


def _validated_image_content_type(
    image_bytes: bytes,
    allowed_content_types: set[str],
) -> str:
    content_type = detect_image_content_type(image_bytes)
    if content_type not in allowed_content_types:
        raise IntegrationUrlDownloadError("远程文件不是支持的 JPEG、PNG 或 WebP 图片")
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(BytesIO(image_bytes)) as image:
            decoded_content_type = pillow_format_to_content_type(image.format)
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError) as exc:
        raise IntegrationUrlDownloadError("远程图片无法解码或已损坏") from exc
    if decoded_content_type != content_type:
        raise IntegrationUrlDownloadError("远程图片格式与文件内容不一致")
    return content_type


def _validate_public_http_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise IntegrationUrlDownloadError("imageUrl 必须是有效的 http/https URL")
    if parsed.username or parsed.password:
        raise IntegrationUrlDownloadError("imageUrl 不允许包含用户名或密码")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        raise IntegrationUrlDownloadError("imageUrl 不允许访问本机或内网地址")
    try:
        addresses = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise IntegrationUrlDownloadError("imageUrl 域名无法解析") from exc
    if not addresses:
        raise IntegrationUrlDownloadError("imageUrl 域名没有可用地址")
    for address in addresses:
        raw_ip = address[4][0].split("%", 1)[0]
        if not ipaddress.ip_address(raw_ip).is_global:
            raise IntegrationUrlDownloadError("imageUrl 不允许访问本机或内网地址")
