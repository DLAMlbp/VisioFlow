import logging
import os

from celery import Celery
from celery.signals import task_postrun, task_prerun, worker_process_init, worker_ready

from src.core.config import get_settings

settings = get_settings()

_ALL_TASK_IMPORTS = (
    "src.workers.control",
    "src.workers.callbacks",
    "src.workers.preprocess",
    "src.workers.completion",
    "src.workers.processing",
    "src.workers.beautify_plan",
    "src.workers.enhance",
    "src.workers.analysis",
    "src.workers.matching",
    "src.workers.library",
    "src.workers.cleanup",
)

_ROLE_TASK_IMPORTS: dict[str, tuple[str, ...]] = {
    "control": ("src.workers.control", "src.workers.callbacks"),
    "preprocess": ("src.workers.preprocess",),
    # The combined path only uses classification. The legacy filtering task is
    # registered here as a rollback queue without keeping another container.
    "classification": ("src.workers.completion", "src.workers.processing"),
    "filtering": ("src.workers.processing",),
    "beautify_plan": ("src.workers.beautify_plan",),
    "enhance": ("src.workers.enhance",),
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
    task_reject_on_worker_lost=True,
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
        "options": {"queue": "control"},
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
def mark_pipeline_recovery_lease_started(task=None, args=None, **_kwargs) -> None:
    if task is None or not args:
        return
    from src.services.jobs.dispatch import mark_recovery_lease_started

    mark_recovery_lease_started(task.name, str(args[0]))


@task_postrun.connect
def release_pipeline_recovery_lease(task=None, args=None, **_kwargs) -> None:
    if task is None or not args:
        return
    from src.services.jobs.dispatch import release_recovery_lease_for_task

    release_recovery_lease_for_task(task.name, str(args[0]))
