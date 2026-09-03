from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.core.config import Settings


def job_deadline(
    settings: Settings,
    image_count: int,
    *,
    now: datetime | None = None,
) -> datetime:
    """Scale the job deadline with batch size while preserving the base allowance."""

    started_at = now or datetime.now(UTC)
    timeout_seconds = settings.pipeline_job_timeout_seconds + (
        max(0, image_count) * settings.pipeline_job_timeout_per_image_seconds
    )
    return started_at + timedelta(seconds=timeout_seconds)
