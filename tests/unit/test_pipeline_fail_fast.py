from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from pydantic import ValidationError

from src.core.config import Settings, get_settings
from src.models.image_item import ImageItem
from src.models.image_job import ImageJob
from src.workers import cleanup
from src.workers.celery_app import celery_app
from src.workers.failures import _failure_code
from src.workers.inpaint import inpaint_watermark
from src.workers.redaction import detect_redaction
from src.workers.render import render_image


def test_pipeline_tasks_are_single_attempt_with_bounded_time() -> None:
    annotations = celery_app.conf.task_annotations
    runtime_settings = get_settings()
    assert annotations["image.classify_completion"] == {
        "max_retries": 0,
        "soft_time_limit": runtime_settings.pipeline_ai_timeout_seconds,
        "time_limit": runtime_settings.pipeline_ai_timeout_seconds + 5,
    }
    assert annotations["image.preprocess_metadata"] == {
        "max_retries": 0,
        "soft_time_limit": 60,
        "time_limit": 65,
    }
    assert annotations["image.detect_redaction"] == {
        "max_retries": 0,
        "soft_time_limit": 60,
        "time_limit": 65,
    }
    assert annotations["image.inpaint_watermark"] == {
        "max_retries": 0,
        "soft_time_limit": 180,
        "time_limit": 185,
    }
    assert annotations["image.render_image"] == {
        "max_retries": 0,
        "soft_time_limit": 60,
        "time_limit": 65,
    }
    assert detect_redaction.max_retries == 0
    assert inpaint_watermark.max_retries == 0
    assert render_image.max_retries == 0
    assert celery_app.conf.task_reject_on_worker_lost is False


def test_transport_retries_are_bounded_and_schema_retries_stay_disabled() -> None:
    settings = Settings(_env_file=None)
    assert settings.ai_tagging_max_retries == 2
    assert settings.ai_processing_schema_max_retries == 0
    assert settings.callback_max_attempts == 3
    assert Settings(_env_file=None, ai_tagging_max_retries=3).ai_tagging_max_retries == 3
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ai_tagging_max_retries=4)


def test_stalled_ai_node_is_reported_after_configured_timeout() -> None:
    now = datetime.now(UTC)
    item = ImageItem(
        id="img_stalled",
        job_id="job_stalled",
        object_key="uploads/stalled.jpg",
        status="analyzing",
        completion_status="processing",
        completion_started_at=now - timedelta(seconds=301),
        updated_at=now - timedelta(seconds=301),
    )
    job = ImageJob(
        id="job_stalled",
        status="processing",
        filter_profile_id="global_filter_v1",
        beautify_profile_id="integration_natural_v1",
        similarity_profile_id="library_similarity_v2",
        enhance_level=1,
        max_selected=1,
        total_count=1,
        updated_at=now,
    )

    node, started_at, timeout = cleanup._stalled_node(
        item, job, now, Settings(_env_file=None)
    )

    assert node == "classification"
    assert started_at == item.completion_started_at
    assert timeout == 300


def test_pending_ai_work_is_not_timed_out_while_waiting_in_queue() -> None:
    now = datetime.now(UTC)
    item = ImageItem(
        id="img_pending",
        job_id="job_pending",
        object_key="uploads/pending.jpg",
        status="analyzing",
        completion_status="pending",
        updated_at=now - timedelta(minutes=10),
    )
    job = ImageJob(
        id="job_pending",
        status="processing",
        filter_profile_id="global_filter_v1",
        beautify_profile_id="integration_natural_v1",
        similarity_profile_id="library_similarity_v2",
        enhance_level=1,
        max_selected=1,
        total_count=1,
        updated_at=now,
    )

    assert cleanup._stalled_node(item, job, now, Settings(_env_file=None)) is None


def test_render_timeout_uses_current_enhancement_stage_start() -> None:
    now = datetime.now(UTC)
    stage_started_at = now - timedelta(seconds=61)
    item = ImageItem(
        id="img_render",
        job_id="job_render",
        object_key="uploads/render.jpg",
        status="enhancing",
        enhancement_stage="render",
        enhancement_stage_started_at=stage_started_at,
        enhance_started_at=now - timedelta(minutes=5),
        updated_at=now,
    )
    job = ImageJob(
        id="job_render",
        status="processing",
        filter_profile_id="global_filter_v1",
        beautify_profile_id="integration_natural_v1",
        similarity_profile_id="library_similarity_v2",
        enhance_level=1,
        max_selected=1,
        total_count=1,
        updated_at=now,
    )

    node, started_at, timeout = cleanup._stalled_node(
        item, job, now, Settings(_env_file=None)
    )

    assert node == "render"
    assert started_at == stage_started_at
    assert timeout == 60


def test_timeout_failure_has_stable_error_code() -> None:
    assert _failure_code(SoftTimeLimitExceeded()) == ("NODE_TIMEOUT", None)


class _CleanupResult:
    def __init__(self, *, scalars=(), scalar=None) -> None:
        self._scalars = scalars
        self._scalar = scalar

    def scalars(self):
        return self._scalars

    def scalar_one_or_none(self):
        return self._scalar


class _CleanupSession:
    def __init__(self, job, item) -> None:
        self._results = iter(
            (
                _CleanupResult(scalars=[job]),
                _CleanupResult(scalar=item),
            )
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def execute(self, _statement):
        return next(self._results)


def _stalled_render_job(now: datetime) -> tuple[ImageJob, ImageItem]:
    job = ImageJob(
        id="job_render_recovery",
        status="enhancing",
        filter_profile_id="global_filter_v1",
        beautify_profile_id="integration_natural_v1",
        similarity_profile_id="library_similarity_v2",
        enhance_level=1,
        max_selected=1,
        total_count=1,
        created_at=now - timedelta(minutes=5),
        updated_at=now,
    )
    item = ImageItem(
        id="img_render_recovery",
        job_id=job.id,
        object_key="uploads/render-recovery.jpg",
        status="enhancing",
        enhancement_stage="render",
        enhancement_stage_started_at=now - timedelta(seconds=61),
        updated_at=now,
    )
    return job, item


@pytest.mark.asyncio
async def test_stalled_render_is_republished_without_failing_job(monkeypatch) -> None:
    now = datetime.now(UTC)
    job, item = _stalled_render_job(now)
    calls: list[tuple] = []

    class Repository:
        def __init__(self, _session) -> None:
            pass

        async def recover_enhancement_stage(self, image_id, **kwargs):
            calls.append(("recover", image_id, kwargs))
            return 1

        async def fail_job(self, *args, **kwargs) -> None:
            calls.append(("fail", args, kwargs))

    class Publisher:
        def publish(self, image_id: str) -> None:
            calls.append(("publish", image_id))

    monkeypatch.setattr(cleanup, "AsyncSessionLocal", lambda: _CleanupSession(job, item))
    monkeypatch.setattr(cleanup, "ImageJobRepository", Repository)
    monkeypatch.setattr(cleanup, "_enhancement_recovery_publisher", lambda _stage: Publisher())
    monkeypatch.setattr(
        cleanup,
        "release_recovery_lease",
        lambda publisher, image_id: calls.append(("release", publisher, image_id)),
    )
    monkeypatch.setattr(
        cleanup,
        "get_settings",
        lambda: SimpleNamespace(
            pipeline_task_timeout_seconds=60,
            pipeline_ai_timeout_seconds=300,
            pipeline_inpaint_timeout_seconds=180,
            pipeline_enhancement_max_recovery_attempts=2,
        ),
    )

    await cleanup._recover_stalled_images()

    assert calls[0][0:2] == ("recover", item.id)
    assert calls[0][2]["expected_stage"] == "render"
    assert calls[0][2]["max_attempts"] == 2
    assert calls[1:] == [
        ("release", "Publisher", item.id),
        ("publish", item.id),
    ]


@pytest.mark.asyncio
async def test_stalled_render_fails_only_current_image_after_recovery_budget_is_exhausted(
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    job, item = _stalled_render_job(now)
    failures: list[tuple] = []

    class Repository:
        def __init__(self, _session) -> None:
            pass

        async def recover_enhancement_stage(self, _image_id, **_kwargs):
            return None

        async def claim_exhausted_enhancement_failure(self, _image_id, **_kwargs):
            return True

        async def fail_item(self, *args, **kwargs) -> None:
            failures.append((args, kwargs))

    monkeypatch.setattr(cleanup, "AsyncSessionLocal", lambda: _CleanupSession(job, item))
    monkeypatch.setattr(cleanup, "ImageJobRepository", Repository)
    monkeypatch.setattr(cleanup, "_enhancement_recovery_publisher", lambda _stage: object())
    monkeypatch.setattr(
        cleanup,
        "get_settings",
        lambda: SimpleNamespace(
            pipeline_task_timeout_seconds=60,
            pipeline_ai_timeout_seconds=300,
            pipeline_inpaint_timeout_seconds=180,
            pipeline_enhancement_max_recovery_attempts=2,
        ),
    )

    await cleanup._recover_stalled_images()

    assert len(failures) == 1
    args, kwargs = failures[0]
    assert args[0] is item
    assert kwargs["node"] == "render"
    assert kwargs["code"] == "ENHANCEMENT_RECOVERY_EXHAUSTED"


@pytest.mark.asyncio
async def test_cleanup_does_not_fail_render_that_advanced_concurrently(monkeypatch) -> None:
    now = datetime.now(UTC)
    job, item = _stalled_render_job(now)
    failed = False

    class Repository:
        def __init__(self, _session) -> None:
            pass

        async def recover_enhancement_stage(self, _image_id, **_kwargs):
            return None

        async def claim_exhausted_enhancement_failure(self, _image_id, **_kwargs):
            return False

        async def fail_job(self, *_args, **_kwargs) -> None:
            nonlocal failed
            failed = True

    monkeypatch.setattr(cleanup, "AsyncSessionLocal", lambda: _CleanupSession(job, item))
    monkeypatch.setattr(cleanup, "ImageJobRepository", Repository)
    monkeypatch.setattr(cleanup, "_enhancement_recovery_publisher", lambda _stage: object())
    monkeypatch.setattr(
        cleanup,
        "get_settings",
        lambda: SimpleNamespace(
            pipeline_task_timeout_seconds=60,
            pipeline_ai_timeout_seconds=300,
            pipeline_inpaint_timeout_seconds=180,
            pipeline_enhancement_max_recovery_attempts=2,
        ),
    )

    await cleanup._recover_stalled_images()

    assert failed is False
