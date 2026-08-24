from typing import Protocol

from src.workers.celery_app import celery_app


class MetadataTaskPublisher(Protocol):
    def publish(self, image_id: str) -> None: ...


class CeleryMetadataTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task("image.preprocess_metadata", args=[image_id], queue="preprocess")


class TaggingTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task("image.generate_tags", args=[image_id], queue="tagging")


class EnhancementBatchTaskPublisher:
    def publish(self, job_id: str) -> None:
        celery_app.send_task("image.enhance_batch", args=[job_id], queue="enhance")
