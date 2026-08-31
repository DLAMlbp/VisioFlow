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
        celery_app.send_task("image.generate_embedding", args=[image_id], queue="embedding")


class MatchTaskPublisher:
    def publish(self, image_id: str) -> None:
        celery_app.send_task("image.match_library", args=[image_id], queue="matching")


class LibraryAssetTaskPublisher:
    def publish(self, asset_id: str) -> None:
        celery_app.send_task("library.process_asset", args=[asset_id], queue="library")


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
