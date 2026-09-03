from __future__ import annotations

import asyncio
import re
import urllib.error

from billiard.exceptions import TimeLimitExceeded
from celery.exceptions import SoftTimeLimitExceeded

from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository

PIPELINE_TASK_NODES: dict[str, tuple[str, str]] = {
    "image.dispatch_job": ("job", "dispatch"),
    "image.preprocess_metadata": ("image", "preprocess"),
    "image.classify_completion": ("image", "classification"),
    "image.apply_routed_processing": ("image", "filtering"),
    "image.rank_job": ("job", "ranking"),
    "image.plan_beautify": ("image", "beautify_planning"),
    "image.enhance": ("image", "beautifying"),
    "image.analyze_content": ("image", "content_analysis"),
    "image.generate_embedding": ("image", "embedding"),
    "image.match_library": ("image", "matching"),
    "image.generate_tags": ("image", "content_analysis"),
}


def record_pipeline_task_failure(
    task_name: str,
    identifier: str,
    exc: BaseException,
    *,
    duration_ms: int | None = None,
) -> None:
    task = PIPELINE_TASK_NODES.get(task_name)
    if task is None:
        return
    identifier_kind, node = task
    code, upstream_status = _failure_code(exc)
    asyncio.run(
        _record_failure(
            identifier_kind,
            identifier,
            node=node,
            code=code,
            reason=_failure_message(node, exc),
            duration_ms=duration_ms,
            upstream_status_code=upstream_status,
        )
    )


async def _record_failure(
    identifier_kind: str,
    identifier: str,
    *,
    node: str,
    code: str,
    reason: str,
    duration_ms: int | None,
    upstream_status_code: int | None,
) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        if identifier_kind == "job":
            await repository.fail_job(
                identifier,
                node=node,
                code=code,
                reason=reason,
                duration_ms=duration_ms,
                upstream_status_code=upstream_status_code,
            )
            return
        item = await repository.get_item(identifier)
        if item is not None:
            await repository.fail_item(
                item,
                node=node,
                code=code,
                reason=reason,
                duration_ms=duration_ms,
                upstream_status_code=upstream_status_code,
            )


def _failure_code(exc: BaseException) -> tuple[str, int | None]:
    if isinstance(exc, (SoftTimeLimitExceeded, TimeLimitExceeded, TimeoutError)):
        return "NODE_TIMEOUT", None
    if isinstance(exc, urllib.error.HTTPError):
        return "UPSTREAM_HTTP_ERROR", exc.code
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return "UPSTREAM_HTTP_ERROR", status
    match = re.search(r"\bHTTP\s+(\d{3})\b", str(exc), flags=re.IGNORECASE)
    if match:
        return "UPSTREAM_HTTP_ERROR", int(match.group(1))
    return "NODE_FAILED", None


def _failure_message(node: str, exc: BaseException) -> str:
    if isinstance(exc, (SoftTimeLimitExceeded, TimeLimitExceeded, TimeoutError)):
        return f"节点 {node} 执行超时，任务已停止"
    detail = " ".join(str(exc).split())[:700]
    return f"节点 {node} 执行失败：{detail or type(exc).__name__}"
