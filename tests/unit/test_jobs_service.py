from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.models.image_item import ImageItem
from src.models.image_job import ImageJob
from src.models.image_metric import ImageMetric
from src.models.image_result import ImageResult
from src.models.image_similarity_match import ImageSimilarityMatch
from src.repositories.jobs import JobProgressSnapshot, terminal_job_status
from src.schemas.jobs import CreateImageJobRequest
from src.services.jobs.service import (
    ImageJobService,
    InvalidJobRequest,
    JobNotFound,
    SelectedImageDownload,
    _beautify_response,
    _classification_response,
)
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


@pytest.fixture(autouse=True)
def mock_global_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve_global_filter(_self):
        return (
            SimpleNamespace(id="flt_global"),
            {
                "id": "flt_global",
                "version": 1,
                "instruction": "排除不符合全局要求的图片",
                "config": {},
            },
        )

    monkeypatch.setattr(
        ManagedProfileService,
        "resolve_global_filter",
        resolve_global_filter,
    )


def _route() -> dict[str, str]:
    return {
        "completion_profile": "completion_renovation_v1",
        "completed_filter_profile": "std_finished",
        "non_completed_filter_profile": "std_unfinished",
    }


def make_payload(image_count: int = 2) -> CreateImageJobRequest:
    return CreateImageJobRequest(
        filter_route=_route(),
        beautify_profile="natural_v1",
        enhance_level=1,
        max_selected=10,
        images=[
            {"object_key": f"uploads/2026/08/19/image_{index}.jpg"} for index in range(image_count)
        ],
    )


def test_terminal_job_status_distinguishes_all_failed_and_partial_failed() -> None:
    assert terminal_job_status(total_count=3, failed_count=3) == "failed"
    assert terminal_job_status(total_count=3, failed_count=1) == "partial_failed"
    assert terminal_job_status(total_count=3, failed_count=0) == "completed"


@pytest.mark.asyncio
async def test_create_job_persists_job_and_image_items() -> None:
    repository = FakeJobRepository()
    service = ImageJobService(
        repository=repository,
        settings=Settings(
            max_images_per_job=50,
            ai_tagging_enabled=True,
        ),
    )

    response = await service.create_job(make_payload())

    assert response.job_id.startswith("job_")
    assert response.status == "queued"
    assert response.total == 2
    assert repository.item_count == 2
    assert repository.jobs[response.job_id].ai_tagging_model == "gpt-5.6-luna"
    assert repository.jobs[response.job_id].similarity_profile_id == "library_similarity_v2"
    assert repository.jobs[response.job_id].watermark_processing_enabled is True


@pytest.mark.asyncio
async def test_create_job_freezes_disabled_watermark_processing() -> None:
    repository = FakeJobRepository()
    service = ImageJobService(repository=repository, settings=Settings())
    payload = make_payload(1)
    payload.watermark_processing_enabled = False

    response = await service.create_job(payload)

    assert repository.jobs[response.job_id].watermark_processing_enabled is False


@pytest.mark.asyncio
async def test_create_job_rejects_disabled_required_stage_configuration() -> None:
    with pytest.raises(ValidationError, match="正式模式固定执行"):
        CreateImageJobRequest(
            filter_route=_route(),
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

    async def resolve_standards(_self, profile_ids=None, *, require_fallback=False):
        assert profile_ids is None
        assert require_fallback is True
        return [
            (
                SimpleNamespace(id="std_finished"),
                {"id": "std_finished", "instruction": "standard-std_finished"},
            ),
            (
                SimpleNamespace(id="std_unfinished"),
                {"id": "std_unfinished", "instruction": "standard-std_unfinished"},
            ),
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
    payload.beautify_profile = "bty_user"
    payload.similarity_profile = "library_similarity_v1"

    response = await service.create_job(payload)

    stored = repository.jobs[response.job_id]
    assert stored.routing_mode == "streaming_v2"
    assert stored.max_selected == len(payload.images)
    assert stored.similarity_profile_id == "library_similarity_v1"
    assert stored.processing_standard_snapshots is not None
    assert [item["id"] for item in stored.processing_standard_snapshots] == [
        "std_finished",
        "std_unfinished",
    ]
    assert stored.beautify_profile_snapshot is not None
    assert stored.beautify_profile_snapshot["id"] == "bty_user"
    assert stored.filter_profile_id == "flt_global"
    assert stored.filter_profile_snapshot is not None
    assert stored.filter_profile_snapshot["instruction"] == "排除不符合全局要求的图片"


@pytest.mark.asyncio
async def test_create_job_uses_server_beautify_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeJobRepository()
    repository.session = object()

    async def resolve_standards(_self, profile_ids=None, *, require_fallback=False):
        return [
            (
                SimpleNamespace(id="std_fallback"),
                {
                    "id": "std_fallback",
                    "instruction": "fallback",
                    "config": {"is_fallback": True},
                },
            )
        ]

    async def resolve_beautify(_self, profile_id: str):
        assert profile_id == "bty_server_default"
        return object(), {"id": profile_id, "instruction": "default", "config": {}}

    monkeypatch.setattr(ManagedProfileService, "resolve_standards", resolve_standards)
    monkeypatch.setattr(ManagedProfileService, "resolve_beautify", resolve_beautify)
    monkeypatch.setattr(ProfileLoader, "get_similarity_profile", lambda *_args: object())
    settings = Settings(
        profiles_directory="profiles",
        integration_beautify_profile="bty_server_default",
    )
    service = ImageJobService(
        repository=repository,
        settings=settings,
        profile_loader=ProfileLoader(settings),
    )
    payload = CreateImageJobRequest(
        images=[{"object_key": "uploads/2026/09/01/default.jpg"}],
    )

    response = await service.create_job(payload)

    stored = repository.jobs[response.job_id]
    assert stored.beautify_profile_id == "bty_server_default"
    assert stored.beautify_profile_snapshot["id"] == "bty_server_default"


@pytest.mark.asyncio
async def test_streaming_job_does_not_require_legacy_batch_barrier() -> None:
    service = ImageJobService(
        repository=FakeJobRepository(),
        settings=Settings(batch_filter_barrier_enabled=False),
    )

    response = await service.create_job(make_payload())

    assert service.repository.jobs[response.job_id].routing_mode == "streaming_v2"


@pytest.mark.asyncio
async def test_legacy_request_fields_do_not_override_all_active_standards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeJobRepository()
    repository.session = object()

    async def resolve_standards(_self, profile_ids=None, *, require_fallback=False):
        assert profile_ids is None
        assert require_fallback is True
        return [
            (SimpleNamespace(id="done"), {"id": "done", "version": 3}),
            (SimpleNamespace(id="work"), {"id": "work", "version": 4}),
            (SimpleNamespace(id="other"), {"id": "other", "version": 5}),
        ]

    async def resolve_beautify(_self, profile_id: str):
        return object(), {"id": profile_id, "instruction": "自然提亮", "config": {}}

    monkeypatch.setattr(ManagedProfileService, "resolve_standards", resolve_standards)
    monkeypatch.setattr(ManagedProfileService, "resolve_beautify", resolve_beautify)
    monkeypatch.setattr(ProfileLoader, "get_similarity_profile", lambda *_args: object())
    service = ImageJobService(
        repository=repository,
        settings=Settings(profiles_directory="profiles"),
        profile_loader=ProfileLoader(Settings(profiles_directory="profiles")),
    )
    payload = CreateImageJobRequest(
        filter_route={
            "completion_profile": "cmp",
            "completed_filter_profile": "done",
            "non_completed_filter_profile": "work",
        },
        beautify_profile="beautify",
        images=[{"object_key": "uploads/test/image.jpg"}],
    )

    response = await service.create_job(payload)
    stored = repository.jobs[response.job_id]

    assert stored.routing_mode == "streaming_v2"
    assert [snapshot["id"] for snapshot in stored.processing_standard_snapshots] == [
        "done",
        "work",
        "other",
    ]
    assert stored.completion_profile_snapshot is None


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


def test_pipeline_progress_is_monotonic_across_new_stages() -> None:
    stages = (
        "classifying",
        "filtering",
        "beautify_planning",
        "beautifying",
        "matching",
        "completed",
    )
    values = [ImageJobService._calculate_progress({stage: 1}, 1, "processing") for stage in stages]

    assert values == sorted(values)
    assert len(set(values)) == len(values)


@pytest.mark.asyncio
async def test_get_progress_raises_for_missing_job() -> None:
    service = ImageJobService(
        repository=FakeJobRepository(), settings=Settings(max_images_per_job=50)
    )

    with pytest.raises(JobNotFound):
        await service.get_progress("job_missing")


@pytest.mark.asyncio
async def test_get_selected_downloads_only_returns_retained_enhanced_images() -> None:
    selected = ImageItem(id="img_selected", job_id="job_download", object_key="uploads/source.jpg")
    selected.client_object_key = "uploads/2026/09/02/客厅 原图.png"
    selected.result = ImageResult(
        id="res_selected",
        image_id=selected.id,
        decision="selected",
        enhanced_object_key="enhanced/job_download/img_selected.jpg",
    )
    rejected = ImageItem(id="img_rejected", job_id="job_download", object_key="uploads/rejected.jpg")
    rejected.result = ImageResult(id="res_rejected", image_id=rejected.id, decision="rejected")
    missing_enhanced = ImageItem(id="img_missing", job_id="job_download", object_key="uploads/missing.jpg")
    missing_enhanced.result = ImageResult(
        id="res_missing", image_id=missing_enhanced.id, decision="selected"
    )
    expired = ImageItem(id="img_expired", job_id="job_download", object_key="uploads/expired.jpg")
    expired.purged_at = datetime.now(UTC)
    expired.result = ImageResult(
        id="res_expired",
        image_id=expired.id,
        decision="selected",
        enhanced_object_key="enhanced/job_download/img_expired.jpg",
    )
    job = ImageJob(
        id="job_download",
        status="completed",
        filter_profile_id="filter",
        beautify_profile_id="beautify",
        enhance_level=1,
        max_selected=4,
        total_count=4,
        processed_count=4,
        selected_count=3,
        rejected_count=1,
        items=[selected, rejected, missing_enhanced, expired],
    )
    repository = FakeJobRepository()
    repository.jobs[job.id] = job
    service = ImageJobService(repository=repository, settings=Settings(max_images_per_job=50))

    downloads = await service.get_selected_downloads(job.id, [selected.id, rejected.id, expired.id])

    assert downloads == [
        SelectedImageDownload(
            object_key="enhanced/job_download/img_selected.jpg",
            archive_filename="001_客厅 原图.jpg",
        )
    ]


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
    settings = Settings(ai_tagging_enabled=True)
    service = ImageJobService(repository=repository, settings=settings)

    response = await service.list_history(limit=30, offset=0)

    assert response.total == 2
    assert [item.job_id for item in response.items] == ["job_newer", "job_older"]
    assert response.items[0].processed == 1
    assert response.items[0].ai_tagging_model == settings.ai_tagging_model


@pytest.mark.asyncio
async def test_create_job_rejects_missing_managed_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeJobRepository()
    repository.session = object()

    async def missing_standards(*_args, **_kwargs):
        raise ProfileNotFoundError("筛选标准不存在或已停用: missing")

    monkeypatch.setattr(ManagedProfileService, "resolve_standards", missing_standards)
    service = ImageJobService(
        repository=repository,
        settings=Settings(max_images_per_job=50, profiles_directory="profiles"),
        profile_loader=ProfileLoader(Settings(profiles_directory="profiles")),
    )
    payload = make_payload()
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
    item.similarity_match = ImageSimilarityMatch(
        id="match_test",
        image_id=item.id,
        matched_asset_id=None,
        matched_tags_snapshot=[],
        similarity_score=0.75,
        feature_score=0.18,
        final_score=0.579,
        decision="pending_review",
        message="图片与内容特征候选需要人工确认",
        candidate_json=[],
    )
    item.ai_processing_json = {
        "filter": {
            "decision": "pass",
            "reason": "全部审核维度合格",
            "confidence": 0.95,
            "dimensions": [{"dimension": "画面清晰度", "passed": True, "reason": "主体清晰"}],
        },
        "beautify": {"needed": False, "reason": "无需调整", "parameters": {}},
        "content": {"summary": "完工客厅", "confidence": 0.9},
    }
    job = ImageJob(
        id="job_test",
        status="completed",
        filter_profile_id="renovation_submission_v1",
        beautify_profile_id="renovation_natural_v1",
        similarity_enabled=True,
        similarity_profile_id="library_similarity_v2",
        enhance_level=1,
        max_selected=10,
        total_count=1,
        processed_count=1,
        selected_count=1,
        rejected_count=0,
        items=[item],
    )
    class ResultsRepository(FakeJobRepository):
        async def get_config(self, job_id: str):
            configured_job = self.jobs[job_id]
            return SimpleNamespace(
                processing_standard_snapshots=None,
                similarity_enabled=True,
                similarity_profile_id=configured_job.similarity_profile_id,
            )

    repository = ResultsRepository()
    repository.jobs[job.id] = job
    settings = Settings(max_images_per_job=50, profiles_directory="profiles")
    service = ImageJobService(
        repository=repository,
        settings=settings,
        profile_loader=ProfileLoader(settings),
    )

    response = await service.get_results(job.id)

    assert response.selected == 1
    assert response.images[0].enhanced_object_key == "enhanced/job_test/img_test.jpg"
    assert response.images[0].original_preview_object_key == "thumbnails/job_test/img_test.jpg"
    assert response.images[0].enhanced_preview_object_key == "analysis/job_test/img_test.jpg"
    assert response.images[0].metrics is not None
    assert response.images[0].metrics.sharpness == 80
    assert response.images[0].enhanced_metrics is not None
    assert response.images[0].tagging_result is not None
    assert response.images[0].tagging_result.auto_threshold == 0.6
    assert response.images[0].tagging_result.review_threshold == 0.45
    assert response.images[0].enhanced_metrics.exposure == 92
    assert response.images[0].audit_dimensions[0].dimension == "分类 · 画面清晰度"
    assert response.images[0].audit_dimensions[0].passed is True


def test_beautify_response_returns_plan_execution_and_acceptance_audit() -> None:
    item = SimpleNamespace(
        beautify_plan_status="completed",
        beautify_plan_error=None,
        beautify_plan_json={
            "decision": {
                "needed": True,
                "reason": "暗部需要适度打开",
                "confidence": 0.93,
                "parameters": {"brightness": 1.08, "shadow_lift": 0.15},
                "parameter_reasons": {},
                "risk_flags": [],
            },
            "effective_parameters": {"brightness": 1.08, "shadow_lift": 0.15},
            "corrections": [],
        },
    )
    result = SimpleNamespace(
        enhancement_audit_json={
            "effective_parameters": {"brightness": 1.03, "shadow_lift": 0.15},
            "corrections": ["小图预演发现曝光风险，已降低亮度"],
            "preview_attempts": [{"attempt": 1}, {"attempt": 2}],
            "acceptance": {
                "status": "passed",
                "checks": [
                    {
                        "name": "exposure",
                        "passed": True,
                        "before": {"brightness": 90.0},
                        "after": {"brightness": 100.0},
                        "reason": "验收通过",
                    }
                ],
                "fallback_reason": None,
            },
            "redaction": {
                "watermark": {
                    "enabled": True,
                    "status": "applied",
                    "outside_roi_changed_pixels": 0,
                },
                "logos": {
                    "enabled": True,
                    "status": "applied",
                    "detections": 2,
                },
            },
        }
    )

    response = _beautify_response(item, result)

    assert response is not None
    assert response.needed is True
    assert response.preview_attempts == 2
    assert response.effective_parameters["brightness"] == 1.03
    assert response.acceptance is not None
    assert response.acceptance.checks[0].name == "exposure"
    assert response.redaction is not None
    assert response.redaction.watermark["outside_roi_changed_pixels"] == 0
    assert response.redaction.logos["detections"] == 2


def test_classification_response_exposes_structured_content_analysis() -> None:
    item = SimpleNamespace(
        routed_filter_profile_id="std_completed",
        completion_confidence=0.97,
        review_required=False,
        completion_json={
            "normalized": {
                "reason": "可见完整的成品客厅空间",
                "content_analysis": {
                    "summary": "家具齐备的完整客厅",
                    "content_type": "室内照片",
                    "scene": "已布置完成的客厅",
                    "spaces": ["客厅"],
                    "view": "整体视角",
                    "subjects": ["客厅空间"],
                    "objects": ["沙发", "茶几"],
                    "visible_conditions": ["墙面与地面完整"],
                    "attributes": {"材质": ["木质", "织物"]},
                    "supporting_evidence": ["未见施工状态"],
                    "conflicting_evidence": [],
                    "missing_evidence": [],
                    "uncertainties": [],
                    "ocr_text": [],
                    "confidence": 0.92,
                },
            }
        },
    )

    response = _classification_response(item, {"std_completed": "完工图片过滤"})

    assert response is not None
    assert response.standard_name == "完工图片过滤"
    assert response.content_analysis is not None
    assert response.content_analysis.objects == ["沙发", "茶几"]
    assert response.content_analysis.confidence == 0.92


def test_classification_response_keeps_legacy_records_readable() -> None:
    item = SimpleNamespace(
        routed_filter_profile_id="std_legacy",
        completion_confidence=0.88,
        review_required=False,
        completion_json={"normalized": {"reason": "历史分类理由"}},
    )

    response = _classification_response(item, {"std_legacy": "历史过滤标准"})

    assert response is not None
    assert response.reason == "历史分类理由"
    assert response.content_analysis is None
