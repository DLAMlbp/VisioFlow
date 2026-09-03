from datetime import UTC, datetime, timedelta

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from pydantic import ValidationError

from src.core.config import Settings
from src.models.image_item import ImageItem
from src.models.image_job import ImageJob
from src.workers import cleanup
from src.workers.celery_app import celery_app
from src.workers.failures import _failure_code


def test_pipeline_tasks_are_single_attempt_with_bounded_time() -> None:
    annotations = celery_app.conf.task_annotations
    assert annotations["image.classify_completion"] == {
        "max_retries": 0,
        "soft_time_limit": 90,
        "time_limit": 95,
    }
    assert annotations["image.preprocess_metadata"] == {
        "max_retries": 0,
        "soft_time_limit": 60,
        "time_limit": 65,
    }
    assert celery_app.conf.task_reject_on_worker_lost is False


def test_retry_settings_cannot_be_enabled() -> None:
    settings = Settings(_env_file=None)
    assert settings.ai_tagging_max_retries == 0
    assert settings.ai_processing_schema_max_retries == 0
    assert settings.callback_max_attempts == 3
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ai_tagging_max_retries=1)


def test_stalled_ai_node_is_reported_after_ninety_seconds() -> None:
    now = datetime.now(UTC)
    item = ImageItem(
        id="img_stalled",
        job_id="job_stalled",
        object_key="uploads/stalled.jpg",
        status="analyzing",
        completion_status="processing",
        completion_started_at=now - timedelta(seconds=91),
        updated_at=now - timedelta(seconds=91),
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
    assert timeout == 90


def test_timeout_failure_has_stable_error_code() -> None:
    assert _failure_code(SoftTimeLimitExceeded()) == ("NODE_TIMEOUT", None)
