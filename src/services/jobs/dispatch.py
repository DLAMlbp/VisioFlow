from typing import Protocol

from src.workers.celery_app import celery_app


class TaskPublisher(Protocol):
    def publish(self, entity_id: str) -> None: ...


class JobDispatchTaskPublisher:
    def publish(self, job_id: str) -> None:
        celery_app.send_task("image.dispatch_job", args=[job_id], queue="control")


class MetadataTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task("image.preprocess_metadata", args=[image_id], queue="preprocess")


class AnalysisTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task(
            "image.analyze_content", args=[image_id], queue="analysis", countdown=0.5
        )


class EmbeddingTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task("image.generate_embedding", args=[image_id], queue="embedding")


class MatchTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task("image.match_library", args=[image_id], queue="embedding")


class LibraryAssetTaskPublisher:
    def publish(self, asset_id: str) -> None:
        celery_app.send_task("library.process_asset", args=[asset_id], queue="library")


class EnhancementTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task("image.enhance", args=[image_id], queue="enhance")


class RankingTaskPublisher:
    def publish(self, job_id: str) -> None:
        celery_app.send_task("image.rank_job", args=[job_id], queue="control")


CeleryMetadataTaskPublisher = MetadataTaskPublisher
TaggingTaskPublisher = AnalysisTaskPublisher
EnhancementBatchTaskPublisher = EnhancementTaskPublisher
