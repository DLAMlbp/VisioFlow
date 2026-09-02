import logging
from functools import lru_cache
from typing import Protocol

from redis import Redis
from redis.exceptions import RedisError

from src.core.config import get_settings
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_RECOVERY_TASK_PUBLISHERS = {
    "image.dispatch_job": "JobDispatchTaskPublisher",
    "image.preprocess_metadata": "MetadataTaskPublisher",
    "image.classify_completion": "CompletionTaskPublisher",
    "image.apply_routed_processing": "RoutedProcessingTaskPublisher",
    "image.rank_job": "RankingTaskPublisher",
    "image.plan_beautify": "BeautifyPlanTaskPublisher",
    "image.enhance": "EnhancementTaskPublisher",
    "image.analyze_content": "AnalysisTaskPublisher",
    "image.generate_embedding": "EmbeddingTaskPublisher",
    "image.match_library": "MatchTaskPublisher",
}


def recovery_lease_key(publisher_name: str, entity_id: str) -> str:
    return f"image-intelligence:pipeline-recovery:{publisher_name}:{entity_id}"


@lru_cache(maxsize=1)
def recovery_lease_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def acquire_recovery_lease(publisher_name: str, entity_id: str) -> bool:
    try:
        return bool(
            recovery_lease_redis().set(
                recovery_lease_key(publisher_name, entity_id),
                "queued",
                nx=True,
                ex=get_settings().pipeline_recovery_lease_seconds,
            )
        )
    except RedisError:
        logger.exception(
            "Recovery lease unavailable; refusing duplicate recovery publish publisher=%s entity_id=%s",
            publisher_name,
            entity_id,
        )
        return False


def mark_recovery_lease_started(task_name: str, entity_id: str) -> None:
    publisher_name = _RECOVERY_TASK_PUBLISHERS.get(task_name)
    if publisher_name is None:
        return
    try:
        recovery_lease_redis().set(
            recovery_lease_key(publisher_name, entity_id),
            "processing",
            xx=True,
            ex=get_settings().pipeline_stale_seconds,
        )
    except RedisError:
        logger.warning(
            "Unable to mark recovery lease started task=%s entity_id=%s",
            task_name,
            entity_id,
            exc_info=True,
        )


def release_recovery_lease_for_task(task_name: str, entity_id: str) -> None:
    publisher_name = _RECOVERY_TASK_PUBLISHERS.get(task_name)
    if publisher_name is not None:
        release_recovery_lease(publisher_name, entity_id)


def release_recovery_lease(publisher_name: str, entity_id: str) -> None:
    try:
        recovery_lease_redis().delete(recovery_lease_key(publisher_name, entity_id))
    except RedisError:
        logger.warning(
            "Unable to roll back recovery lease publisher=%s entity_id=%s",
            publisher_name,
            entity_id,
            exc_info=True,
        )


class TaskPublisher(Protocol):
    def publish(self, entity_id: str) -> None: ...


class JobDispatchTaskPublisher:
    def publish(self, job_id: str) -> None:
        celery_app.send_task("image.dispatch_job", args=[job_id], queue="control")


class MetadataTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task("image.preprocess_metadata", args=[image_id], queue="preprocess")


class CompletionTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task(
            "image.classify_completion", args=[image_id], queue="classification"
        )


class RoutedProcessingTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task(
            "image.apply_routed_processing", args=[image_id], queue="filtering"
        )


class AnalysisTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task(
            "image.analyze_content", args=[image_id], queue="analysis", countdown=0.5
        )


class EmbeddingTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task(
            "image.generate_embedding",
            args=[image_id],
            queue="openclip",
            priority=9,
        )


class ProvisionalEmbeddingTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task(
            "image.generate_embedding",
            args=[image_id],
            queue="openclip",
            priority=5,
        )


class MatchTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task("image.match_library", args=[image_id], queue="matching")


class LibraryAssetTaskPublisher:
    def publish(self, asset_id: str) -> None:
        celery_app.send_task(
            "library.process_asset",
            args=[asset_id],
            queue="openclip",
            priority=1,
        )


class EnhancementTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task("image.enhance", args=[image_id], queue="enhance")


class BeautifyPlanTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task(
            "image.plan_beautify", args=[image_id], queue="beautify_plan"
        )


class RankingTaskPublisher:
    def publish(self, job_id: str) -> None:
        celery_app.send_task("image.rank_job", args=[job_id], queue="control")


class CallbackTaskPublisher:
    def publish(self, job_id: str) -> None:
        celery_app.send_task("image.deliver_callback", args=[job_id], queue="control")


CeleryMetadataTaskPublisher = MetadataTaskPublisher
TaggingTaskPublisher = AnalysisTaskPublisher
EnhancementBatchTaskPublisher = EnhancementTaskPublisher
