import logging
import os
import time

from celery import Celery
from celery.signals import (
    task_failure,
    task_postrun,
    task_prerun,
    worker_process_init,
    worker_ready,
)

from src.core.config import get_settings

settings = get_settings()
_task_started_at: dict[str, float] = {}

_ALL_TASK_IMPORTS = (
    "src.workers.control",
    "src.workers.callbacks",
    "src.workers.preprocess",
    "src.workers.completion",
    "src.workers.processing",
    "src.workers.beautify_plan",
    "src.workers.redaction",
    "src.workers.inpaint",
    "src.workers.enhance",
    "src.workers.render",
    "src.workers.analysis",
    "src.workers.matching",
    "src.workers.library",
    "src.workers.cleanup",
)

_ROLE_TASK_IMPORTS: dict[str, tuple[str, ...]] = {
    "control": ("src.workers.control", "src.workers.callbacks"),
    "callback": ("src.workers.callbacks",),
    "preprocess": ("src.workers.preprocess",),
    # The combined path only uses classification. The legacy filtering task is
    # registered here as a rollback queue without keeping another container.
    "classification": ("src.workers.completion", "src.workers.processing"),
    "filtering": ("src.workers.processing",),
    "beautify_plan": ("src.workers.beautify_plan",),
    "redaction": ("src.workers.redaction",),
    "inpaint": ("src.workers.inpaint",),
    "enhance": ("src.workers.enhance",),
    "render": ("src.workers.render",),
    "analysis": ("src.workers.analysis",),
    # Both task modules share one process and therefore one resident OpenCLIP model.
    "openclip": ("src.workers.matching", "src.workers.library"),
    "matching": ("src.workers.matching",),
    "cleanup": ("src.workers.cleanup",),
    "beat": (),
}


def _task_imports(worker_role: str) -> tuple[str, ...]:
    return _ROLE_TASK_IMPORTS.get(worker_role, _ALL_TASK_IMPORTS)

celery_app = Celery(
    "image_intelligence_service",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=False,
    task_acks_on_failure_or_timeout=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    imports=_task_imports(os.getenv("WORKER_ROLE", "").strip()),
    task_queue_max_priority=9,
    task_default_priority=5,
    broker_transport_options={
        # Redis implements priority with separate physical lists. Keeping all
        # OpenCLIP work on one logical queue makes online-vs-backfill ordering
        # deterministic between individual tasks.
        "priority_steps": list(range(10)),
        "sep": ":",
    },
    task_annotations={
        "image.dispatch_job": {"max_retries": 0, "soft_time_limit": 60, "time_limit": 65},
        "image.preprocess_metadata": {
            "max_retries": 0,
            "soft_time_limit": 60,
            "time_limit": 65,
        },
        "image.classify_completion": {
            "max_retries": 0,
            "soft_time_limit": 90,
            "time_limit": 95,
        },
        "image.apply_routed_processing": {
            "max_retries": 0,
            "soft_time_limit": 90,
            "time_limit": 95,
        },
        "image.rank_job": {"max_retries": 0, "soft_time_limit": 60, "time_limit": 65},
        "image.plan_beautify": {
            "max_retries": 0,
            "soft_time_limit": 90,
            "time_limit": 95,
        },
        "image.enhance": {"max_retries": 0, "soft_time_limit": 60, "time_limit": 65},
        "image.analyze_content": {
            "max_retries": 0,
            "soft_time_limit": 90,
            "time_limit": 95,
        },
        "image.generate_embedding": {
            "max_retries": 0,
            "soft_time_limit": 60,
            "time_limit": 65,
        },
        "image.match_library": {"max_retries": 0, "soft_time_limit": 60, "time_limit": 65},
        "image.generate_tags": {
            "max_retries": 0,
            "soft_time_limit": 90,
            "time_limit": 95,
        },
    },
)

celery_app.conf.beat_schedule = {
    "backfill-library-content-features": {
        "task": "library.backfill_content_features",
        "schedule": 300,
        "options": {"queue": "openclip", "priority": 1},
    },
    "cleanup-expired-images": {
        "task": "maintenance.cleanup_expired_images",
        "schedule": settings.cleanup_interval_seconds,
        "options": {"queue": "cleanup"},
    },
    "recover-stalled-images": {
        "task": "maintenance.recover_stalled_images",
        "schedule": settings.pipeline_recovery_interval_seconds,
        "options": {"queue": "cleanup"},
    },
    "recover-pending-callbacks": {
        "task": "maintenance.recover_pending_callbacks",
        "schedule": settings.callback_recovery_interval_seconds,
        "options": {"queue": "callback"},
    },
}


def _prewarm_embedding_model() -> None:
    if os.getenv("WORKER_ROLE") != "openclip":
        return
    try:
        from src.services.images.embedding import _load_model

        _load_model(
            settings.image_embedding_model,
            settings.image_embedding_pretrained,
            settings.inference_device,
            settings.inference_cpu_threads,
        )
    except Exception:
        logging.getLogger(__name__).exception("Unable to prewarm OpenCLIP")


@worker_process_init.connect
def prewarm_embedding_process(**_kwargs) -> None:
    _prewarm_embedding_model()


@worker_ready.connect
def prewarm_embedding_solo_worker(**_kwargs) -> None:
    # The production OpenCLIP worker uses the solo pool so the Celery parent
    # does not keep a second Python/Torch runtime beside a single child.
    _prewarm_embedding_model()


@task_prerun.connect
def mark_pipeline_recovery_lease_started(task=None, task_id=None, args=None, **_kwargs) -> None:
    if task is None or not args:
        return
    from src.services.jobs.dispatch import mark_recovery_lease_started

    if task_id:
        _task_started_at[str(task_id)] = time.monotonic()
    mark_recovery_lease_started(task.name, str(args[0]))


@task_failure.connect
def fail_pipeline_without_retry(
    sender=None, task_id=None, exception=None, args=None, kwargs=None, **_extras
) -> None:
    if sender is None or exception is None:
        return
    identifier = args[0] if args else (kwargs or {}).get("image_id") or (kwargs or {}).get("job_id")
    if not identifier:
        return
    started = _task_started_at.get(str(task_id)) if task_id else None
    duration_ms = int((time.monotonic() - started) * 1000) if started is not None else None
    from src.workers.failures import record_pipeline_task_failure

    record_pipeline_task_failure(
        sender.name,
        str(identifier),
        exception,
        duration_ms=duration_ms,
    )


@task_postrun.connect
def release_pipeline_recovery_lease(
    task=None, task_id=None, args=None, state=None, **_kwargs
) -> None:
    if task is None or not args:
        return
    if state == "RETRY":
        return
    from src.services.jobs.dispatch import release_recovery_lease_for_task

    if task_id:
        _task_started_at.pop(str(task_id), None)
    release_recovery_lease_for_task(task.name, str(args[0]))
