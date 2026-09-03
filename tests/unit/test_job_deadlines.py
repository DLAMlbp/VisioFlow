from datetime import UTC, datetime, timedelta

from src.core.config import Settings
from src.services.jobs.deadlines import job_deadline


def test_job_deadline_scales_with_batch_size() -> None:
    now = datetime(2026, 9, 3, tzinfo=UTC)
    settings = Settings(
        _env_file=None,
        pipeline_job_timeout_seconds=1800,
        pipeline_job_timeout_per_image_seconds=30,
    )

    assert job_deadline(settings, 10, now=now) == now + timedelta(seconds=2100)
    assert job_deadline(settings, 500, now=now) == now + timedelta(seconds=16800)
