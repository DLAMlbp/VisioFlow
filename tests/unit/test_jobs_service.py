from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.models.image_item import ImageItem
from src.models.image_job import ImageJob
from src.models.image_metric import ImageMetric
from src.models.image_result import ImageResult
from src.repositories.jobs import JobProgressSnapshot
from src.schemas.jobs import CreateImageJobRequest
from src.services.jobs.service import ImageJobService, InvalidJobRequest, JobNotFound
from src.services.managed_profiles import ManagedProfileService
from src.services.profiles import ProfileLoader, ProfileNotFoundError


class FakeJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[str, ImageJob] = {}
        self.item_count = 0

    async def create(self, job: ImageJob, items: list[object]) -> ImageJob:
        self.jobs[job.id] = job
        self.item_count += len(items)
        return job

    async def get(self, job_id: str) -> ImageJob | None:
        return self.jobs.get(job_id)

    async def list_jobs(self, limit: int, offset: int) -> tuple[int, list[ImageJob]]:
        jobs = sorted(self.jobs.values(), key=lambda job: job.created_at, reverse=True)
        return len(jobs), jobs[offset : offset + limit]

    async def get_progress_snapshot(self, job_id: str) -> JobProgressSnapshot | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        waiting = max(0, job.total_count - job.processed_count)
        return JobProgressSnapshot(
            id=job.id,
            status=job.status,
            total_count=job.total_count,
            processed_count=job.processed_count,
            selected_count=job.selected_count,
            rejected_count=job.rejected_count,
            not_selected_count=job.not_selected_count or 0,
            stage_counts={
                "waiting": waiting,
                "completed": job.selected_count,
                "rejected": job.rejected_count,
                "not_selected": job.not_selected_count or 0,
            },
        )

    async def list_result_items(
        self, job_id: str, *, limit: int, offset: int, decision: str | None
    ) -> tuple[int, list[ImageItem]]:
        job = self.jobs[job_id]
        items = [item for item in job.items if item.result is not None]
        if decision:
            items = [item for item in items if item.result and item.result.decision == decision]
        return len(items), items[offset : offset + limit]


class FakeTaskPublisher:
    def __init__(self) -> None:
        self.image_ids: list[str] = []

    def publish(self, image_id: str) -> None:
        self.image_ids.append(image_id)


def make_payload(image_count: int = 2) -> CreateImageJobRequest:
    return CreateImageJobRequest(
        processing_standards=["std_finished", "std_unfinished"],
        beautify_profile="natural_v1",
        enhance_level=1,
        max_selected=10,
        images=[
            {"object_key": f"uploads/2026/08/19/image_{index}.jpg"}
            for index in range(image_count)
        ],
    )


@pytest.mark.asyncio
async def test_create_job_persists_job_and_image_items() -> None:
    repository = FakeJobRepository()
    service = ImageJobService(
        repository=repository,
        settings=Settings(max_images_per_job=50, ai_tagging_model="vision-model-test"),
    )

    response = await service.create_job(make_payload())

    assert response.job_id.startswith("job_")
    assert response.status == "queued"
    assert response.total == 2
    assert repository.item_count == 2
    assert repository.jobs[response.job_id].ai_tagging_model == "vision-model-test"
    assert repository.jobs[response.job_id].similarity_profile_id == "library_similarity_v2"


@pytest.mark.asyncio
async def test_create_job_rejects_disabled_required_stage_configuration() -> None:
    with pytest.raises(ValidationError, match="正式模式固定执行"):
        CreateImageJobRequest(
            processing_standards=["std_finished", "std_unfinished"],
            beautify_profile="natural_v1",
            filter_enabled=False,
            images=[{"object_key": "uploads/2026/08/19/image.jpg"}],
        )


@pytest.mark.asyncio
async def test_create_job_snapshots_managed_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeJobRepository()
    repository.session = object()
    async def resolve_standards(_self, profile_ids: list[str]):
        return [
            (object(), {"id": profile_id, "instruction": f"standard-{profile_id}", "config": {}})
            for profile_id in profile_ids
        ]

    async def resolve_beautify(_self, profile_id: str):
        return object(), {"id": profile_id, "instruction": "自然提亮", "config": {}}

    monkeypatch.setattr(ManagedProfileService, "resolve_standards", resolve_standards)
    monkeypatch.setattr(ManagedProfileService, "resolve_beautify", resolve_beautify)
    monkeypatch.setattr(ProfileLoader, "get_similarity_profile", lambda *_args: object())
    service = ImageJobService(
        repository=repository,
        settings=Settings(max_images_per_job=50, profiles_directory="profiles"),
        profile_loader=ProfileLoader(Settings(profiles_directory="profiles")),
    )
    payload = make_payload()
    payload.processing_standards = ["std_finished", "std_unfinished"]
    payload.beautify_profile = "bty_user"
    payload.similarity_profile = "library_similarity_v1"

    response = await service.create_job(payload)

    stored = repository.jobs[response.job_id]
    assert stored.similarity_profile_id == "library_similarity_v1"
    assert stored.processing_standard_snapshots is not None
    assert [item["id"] for item in stored.processing_standard_snapshots] == [
        "std_finished",
        "std_unfinished",
    ]
    assert stored.beautify_profile_snapshot is not None
    assert stored.beautify_profile_snapshot["id"] == "bty_user"


@pytest.mark.asyncio
async def test_create_job_publishes_one_job_dispatch_task() -> None:
    publisher = FakeTaskPublisher()
    service = ImageJobService(
        repository=FakeJobRepository(),
        settings=Settings(max_images_per_job=50),
        task_publisher=publisher,
    )

    await service.create_job(make_payload())

    assert len(publisher.image_ids) == 1
    assert publisher.image_ids[0].startswith("job_")


@pytest.mark.asyncio
async def test_create_job_rejects_too_many_images() -> None:
    repository = FakeJobRepository()
    service = ImageJobService(repository=repository, settings=Settings(max_images_per_job=1))

    with pytest.raises(InvalidJobRequest, match="最多支持"):
        await service.create_job(make_payload(image_count=2))


@pytest.mark.asyncio
async def test_get_progress_returns_counts() -> None:
    repository = FakeJobRepository()
    service = ImageJobService(repository=repository, settings=Settings(max_images_per_job=50))
    created = await service.create_job(make_payload(image_count=4))

    stored = repository.jobs[created.job_id]
    stored.processed_count = 2
    stored.selected_count = 1
    stored.rejected_count = 1

    progress = await service.get_progress(created.job_id)

    assert progress.progress == 50
    assert progress.total == 4
    assert progress.processed == 2
    assert progress.selected == 1
    assert progress.rejected == 1


@pytest.mark.asyncio
async def test_get_progress_raises_for_missing_job() -> None:
    service = ImageJobService(repository=FakeJobRepository(), settings=Settings(max_images_per_job=50))

    with pytest.raises(JobNotFound):
        await service.get_progress("job_missing")


@pytest.mark.asyncio
async def test_list_history_returns_recent_job_summaries() -> None:
    repository = FakeJobRepository()
    older = ImageJob(
        id="job_older",
        status="completed",
        filter_profile_id="renovation_submission_v1",
        beautify_profile_id="renovation_natural_v1",
        enhance_level=1,
        max_selected=10,
        total_count=2,
        processed_count=2,
        selected_count=1,
        rejected_count=1,
        created_at=datetime(2026, 8, 19, tzinfo=UTC),
    )
    newer = ImageJob(
        id="job_newer",
        status="analyzing",
        filter_profile_id="renovation_submission_v1",
        beautify_profile_id="renovation_natural_v1",
        enhance_level=1,
        max_selected=10,
        total_count=3,
        processed_count=1,
        selected_count=1,
        rejected_count=0,
        created_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    repository.jobs = {older.id: older, newer.id: newer}
    service = ImageJobService(repository=repository, settings=Settings())

    response = await service.list_history(limit=30, offset=0)

    assert response.total == 2
    assert [item.job_id for item in response.items] == ["job_newer", "job_older"]
    assert response.items[0].processed == 1
    assert response.items[0].ai_tagging_model == Settings().ai_tagging_model


@pytest.mark.asyncio
async def test_create_job_rejects_missing_managed_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeJobRepository()
    repository.session = object()

    async def missing_standards(*_args):
        raise ProfileNotFoundError("筛选标准不存在或已停用: missing")

    monkeypatch.setattr(ManagedProfileService, "resolve_standards", missing_standards)
    service = ImageJobService(
        repository=repository,
        settings=Settings(max_images_per_job=50, profiles_directory="profiles"),
        profile_loader=ProfileLoader(Settings(profiles_directory="profiles")),
    )
    payload = make_payload()
    payload.processing_standards = ["missing", "std_unfinished"]

    with pytest.raises(InvalidJobRequest, match="筛选标准不存在"):
        await service.create_job(payload)


@pytest.mark.asyncio
async def test_get_results_returns_decision_metrics_and_enhanced_key() -> None:
    item = ImageItem(
        id="img_test",
        job_id="job_test",
        object_key="uploads/source.jpg",
        thumbnail_object_key="thumbnails/job_test/img_test.jpg",
        analysis_object_key="analysis/job_test/img_test.jpg",
        status="selected",
    )
    item.metric = ImageMetric(
        id="met_test",
        image_id=item.id,
        sharpness_score=80,
        exposure_score=90,
        contrast_score=70,
        noise_score=85,
        raw_metrics_json={},
    )
    item.result = ImageResult(
        id="res_test",
        image_id=item.id,
        decision="selected",
        final_score=80,
        enhanced_object_key="enhanced/job_test/img_test.jpg",
        enhanced_metrics_json={"sharpness": 82, "exposure": 92, "contrast": 75, "noise": 88},
        reasons_json=["通过装修照片基础质量标准并完成自然美化"],
    )
    item.ai_processing_json = {
        "filter": {
            "decision": "pass",
            "reason": "全部审核维度合格",
            "confidence": 0.95,
            "dimensions": [
                {"dimension": "画面清晰度", "passed": True, "reason": "主体清晰"}
            ],
        },
        "beautify": {"needed": False, "reason": "无需调整", "parameters": {}},
        "content": {"summary": "完工客厅", "confidence": 0.9},
    }
    job = ImageJob(
        id="job_test",
        status="completed",
        filter_profile_id="renovation_submission_v1",
        beautify_profile_id="renovation_natural_v1",
        enhance_level=1,
        max_selected=10,
        total_count=1,
        processed_count=1,
        selected_count=1,
        rejected_count=0,
        items=[item],
    )
    repository = FakeJobRepository()
    repository.jobs[job.id] = job
    service = ImageJobService(repository=repository, settings=Settings(max_images_per_job=50))

    response = await service.get_results(job.id)

    assert response.selected == 1
    assert response.images[0].enhanced_object_key == "enhanced/job_test/img_test.jpg"
    assert response.images[0].original_preview_object_key == "thumbnails/job_test/img_test.jpg"
    assert response.images[0].enhanced_preview_object_key == "analysis/job_test/img_test.jpg"
    assert response.images[0].metrics is not None
    assert response.images[0].metrics.sharpness == 80
    assert response.images[0].enhanced_metrics is not None
    assert response.images[0].enhanced_metrics.exposure == 92
    assert response.images[0].audit_dimensions[0].dimension == "画面清晰度"
    assert response.images[0].audit_dimensions[0].passed is True
