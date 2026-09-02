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


def _publish_pipeline_task(
    publisher_name: str,
    entity_id: str,
    task_name: str,
    *,
    queue: str,
    lease_publisher_name: str | None = None,
    **options,
) -> None:
    lease_name = lease_publisher_name or publisher_name
    if not acquire_recovery_lease(lease_name, entity_id):
        return
    try:
        celery_app.send_task(task_name, args=[entity_id], queue=queue, **options)
    except Exception:
        release_recovery_lease(lease_name, entity_id)
        raise


class TaskPublisher(Protocol):
    def publish(self, entity_id: str) -> None: ...


class JobDispatchTaskPublisher:
    def publish(self, job_id: str) -> None:
        _publish_pipeline_task(
            type(self).__name__, job_id, "image.dispatch_job", queue="control"
        )


class MetadataTaskPublisher:
    def publish(self, image_id: str) -> None:
        _publish_pipeline_task(
            type(self).__name__, image_id, "image.preprocess_metadata", queue="preprocess"
        )


class CompletionTaskPublisher:
    def publish(self, image_id: str) -> None:
        _publish_pipeline_task(
            type(self).__name__, image_id, "image.classify_completion", queue="classification"
        )


class RoutedProcessingTaskPublisher:
    def publish(self, image_id: str) -> None:
        _publish_pipeline_task(
            type(self).__name__, image_id, "image.apply_routed_processing", queue="filtering"
        )


class AnalysisTaskPublisher:
    def publish(self, image_id: str) -> None:
        _publish_pipeline_task(
            type(self).__name__,
            image_id,
            "image.analyze_content",
            queue="analysis",
            countdown=0.5,
        )


class EmbeddingTaskPublisher:
    def publish(self, image_id: str) -> None:
        _publish_pipeline_task(
            type(self).__name__,
            image_id,
            "image.generate_embedding",
            queue="openclip",
            priority=9,
        )


class ProvisionalEmbeddingTaskPublisher:
    def publish(self, image_id: str) -> None:
        _publish_pipeline_task(
            type(self).__name__,
            image_id,
            "image.generate_embedding",
            queue="openclip",
            priority=5,
            lease_publisher_name="EmbeddingTaskPublisher",
        )


class MatchTaskPublisher:
    def publish(self, image_id: str) -> None:
        _publish_pipeline_task(
            type(self).__name__, image_id, "image.match_library", queue="matching"
        )


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
        _publish_pipeline_task(type(self).__name__, image_id, "image.enhance", queue="enhance")


class BeautifyPlanTaskPublisher:
    def publish(self, image_id: str) -> None:
        _publish_pipeline_task(
            type(self).__name__, image_id, "image.plan_beautify", queue="beautify_plan"
        )


class RankingTaskPublisher:
    def publish(self, job_id: str) -> None:
        _publish_pipeline_task(type(self).__name__, job_id, "image.rank_job", queue="control")


class CallbackTaskPublisher:
    def publish(self, job_id: str) -> None:
        celery_app.send_task("image.deliver_callback", args=[job_id], queue="callback")


CeleryMetadataTaskPublisher = MetadataTaskPublisher
TaggingTaskPublisher = AnalysisTaskPublisher
EnhancementBatchTaskPublisher = EnhancementTaskPublisher
