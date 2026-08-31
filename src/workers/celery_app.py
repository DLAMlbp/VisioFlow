import logging
import os

from celery import Celery
from celery.signals import worker_process_init

from src.core.config import get_settings

settings = get_settings()

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
    imports=(
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
    ),
)

celery_app.conf.beat_schedule = {
    "backfill-library-content-features": {
        "task": "library.backfill_content_features",
        "schedule": 300,
        "options": {"queue": "library"},
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
    if os.getenv("WORKER_ROLE") != "embedding":
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
