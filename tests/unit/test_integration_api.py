from fastapi.testclient import TestClient

from src.api.jobs import get_job_service
from src.api.uploads import get_storage_provider
from src.core.config import Settings, get_settings
from src.main import app
from src.schemas.jobs import (
    CreateImageJobResponse,
    ImageItemStatus,
    ImageJobProgressResponse,
    ImageJobResultItemResponse,
    ImageJobResultsResponse,
)
from src.services.integration_admission import IntegrationAdmissionRejected
from src.services.integration_urls import StagedIntegrationImage
from src.services.storage.interfaces import StorageProvider


class FakeStorageProvider(StorageProvider):
    def __init__(self) -> None:
        self.uploaded: list[tuple[str, bytes, str]] = []
        self.deleted: list[str] = []

    async def upload(self, object_key: str, data: bytes, content_type: str) -> None:
        self.uploaded.append((object_key, data, content_type))

    async def download(self, object_key: str) -> bytes:
        return b""

    async def get_size(self, object_key: str) -> int:
        return 0

    async def delete(self, object_key: str) -> None:
        self.deleted.append(object_key)

    async def presign_upload(self, object_key: str, content_type: str, expires_seconds: int) -> str:
        return ""

    async def presign_download(self, object_key: str, expires_seconds: int) -> str:
        return f"https://storage.test/{object_key}?expires={expires_seconds}"


class FakeJobService:
    async def create_job(self, payload):
        self.payload = payload
        return CreateImageJobResponse(
            job_id="job_integration", status="queued", total=len(payload.images)
        )

    async def get_progress(self, job_id: str):
        return ImageJobProgressResponse(
            job_id=job_id, status="queued", progress=0, total=1, processed=0, selected=0, rejected=0
        )

    async def get_results(self, job_id: str, *, limit=50, offset=0, decision=None):
        return ImageJobResultsResponse(
            job_id=job_id,
            total=1,
            selected=1,
            rejected=0,
            result_total=1,
            limit=limit,
            offset=offset,
            images=[
                ImageJobResultItemResponse(
                    image_id="img_integration",
                    decision=ImageItemStatus.SELECTED,
                    score=98.0,
                    original_object_key="uploads/job/original.jpg",
                    enhanced_object_key="enhanced/job/result.jpg",
                )
            ],
        )


def _route_form() -> dict[str, str]:
    return {
        "completion_profile": "completion_renovation_v1",
        "completed_filter_profile": "standard_completed_v1",
        "non_completed_filter_profile": "standard_non_completed_v1",
    }


def test_integration_job_uploads_files_and_creates_async_job() -> None:
    storage = FakeStorageProvider()
    jobs = FakeJobService()
    app.dependency_overrides[get_storage_provider] = lambda: storage
    app.dependency_overrides[get_job_service] = lambda: jobs
    app.dependency_overrides[get_settings] = lambda: Settings(integration_api_key="test-integration-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/integration/jobs",
        files=[
            ("files", ("kitchen.jpg", b"one", "image/jpeg")),
            ("files", ("bathroom.png", b"two", "image/png")),
        ],
        data={
            **_route_form(),
            "beautify_profile": "bty_user",
            "watermark_processing_enabled": "true",
            "max_selected": "1",
            "enhance_level": "2",
            "callback_url": "https://client.test/api/image-callback",
        },
        headers={"X-API-Key": "test-integration-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json() == {"job_id": "job_integration", "status": "queued", "total": 2}
    assert len(storage.uploaded) == 2
    assert [image.object_key for image in jobs.payload.images] == [item[0] for item in storage.uploaded]
    assert jobs.payload.enhance_level == 2
    assert jobs.payload.watermark_processing_enabled is True
    assert str(jobs.payload.callback_url) == "https://client.test/api/image-callback"


def test_integration_job_rejects_disabled_required_stages() -> None:
    storage = FakeStorageProvider()
    jobs = FakeJobService()
    app.dependency_overrides[get_storage_provider] = lambda: storage
    app.dependency_overrides[get_job_service] = lambda: jobs
    app.dependency_overrides[get_settings] = lambda: Settings(
        integration_api_key="test-integration-key"
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/integration/file-jobs",
        files=[("files", ("product.jpg", b"one", "image/jpeg"))],
        data={
            **_route_form(),
            "beautify_profile": "bty_user",
            "filter_enabled": "false",
            "beautify_enabled": "false",
            "similarity_enabled": "false",
            "callback_url": "https://client.test/api/image-callback",
        },
        headers={"X-API-Key": "test-integration-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "正式模式固定执行" in response.json()["detail"]


def test_integration_job_accepts_paired_filter_standards() -> None:
    storage = FakeStorageProvider()
    app.dependency_overrides[get_storage_provider] = lambda: storage
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(
        integration_api_key="test-integration-key"
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/integration/file-jobs",
        files=[("files", ("product.jpg", b"one", "image/jpeg"))],
        data={
            "processing_standards": "std_finished,std_unfinished",
            "beautify_profile": "bty_user",
            "callback_url": "https://client.test/api/image-callback",
        },
        headers={"X-API-Key": "test-integration-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["job_id"] == "job_integration"
    assert len(storage.uploaded) == 1


def test_integration_job_rejects_invalid_file_and_removes_prior_uploads() -> None:
    storage = FakeStorageProvider()
    app.dependency_overrides[get_storage_provider] = lambda: storage
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(integration_api_key="test-integration-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/integration/file-jobs",
        files=[
            ("files", ("valid.jpg", b"one", "image/jpeg")),
            ("files", ("invalid.gif", b"two", "image/gif")),
        ],
        data={
            **_route_form(),
            "beautify_profile": "bty_user",
            "callback_url": "https://client.test/api/image-callback",
        },
        headers={"X-API-Key": "test-integration-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "不支持" in response.json()["detail"]
    assert storage.deleted == [storage.uploaded[0][0]]


def test_integration_job_requires_callback_url() -> None:
    storage = FakeStorageProvider()
    app.dependency_overrides[get_storage_provider] = lambda: storage
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(
        integration_api_key="test-integration-key"
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/integration/file-jobs",
        files=[("files", ("kitchen.jpg", b"one", "image/jpeg"))],
        data={
            "processing_standards": "std_finished,std_unfinished",
            "beautify_profile": "bty_user",
        },
        headers={"X-API-Key": "test-integration-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 422


def test_integration_job_accepts_customer_image_urls(monkeypatch) -> None:
    storage = FakeStorageProvider()
    jobs = FakeJobService()

    async def fake_stage(images, *, storage, settings):
        assert [image.object_key for image in images] == ["customer/a.jpg", "customer/b.jpg"]
        return [
            StagedIntegrationImage("uploads/a.jpg", "customer/a.jpg"),
            StagedIntegrationImage("uploads/b.jpg", "customer/b.jpg"),
        ]

    monkeypatch.setattr("src.api.integration.stage_integration_urls", fake_stage)
    app.dependency_overrides[get_storage_provider] = lambda: storage
    app.dependency_overrides[get_job_service] = lambda: jobs
    app.dependency_overrides[get_settings] = lambda: Settings(
        integration_api_key="test-integration-key"
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/integration/jobs",
        json={
            "notifyUrl": "https://client.test/v1/callback/ai/theme-image/notify",
            "watermarkProcessingEnabled": True,
            "images": [
                {"objectKey": "customer/a.jpg", "imageUrl": "https://obs.test/a.jpg"},
                {"objectKey": "customer/b.jpg", "imageUrl": "https://obs.test/b.jpg"},
            ],
        },
        headers={"X-API-Key": "test-integration-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json() == {
        "job_id": "job_integration",
        "status": "queued",
        "total": 2,
        "ok": True,
        "code": "",
        "message": "",
        "taskId": "job_integration",
    }
    assert jobs.payload.callback_contract == "customer_v1"
    assert jobs.payload.watermark_processing_enabled is True
    assert [image.client_object_key for image in jobs.payload.images] == [
        "customer/a.jpg",
        "customer/b.jpg",
    ]
    assert jobs.payload.filter_route.completion_profile == "completion_renovation_v1"
    assert jobs.payload.beautify_profile == "integration_natural_v1"


def test_integration_job_returns_429_before_downloading_images(monkeypatch) -> None:
    staged = False

    async def fake_stage(*_args, **_kwargs):
        nonlocal staged
        staged = True
        return []

    def reject(_settings, _image_count):
        raise IntegrationAdmissionRejected(
            "图片处理队列繁忙，请稍后重试",
            retry_after_seconds=300,
            queue_depth=100,
        )

    monkeypatch.setattr("src.api.integration.stage_integration_urls", fake_stage)
    monkeypatch.setattr("src.api.integration.enforce_integration_admission", reject)
    app.dependency_overrides[get_storage_provider] = lambda: FakeStorageProvider()
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(
        integration_api_key="test-integration-key",
        integration_admission_enabled=True,
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/integration/jobs",
        json={
            "notifyUrl": "https://client.test/callback",
            "images": [{"objectKey": "a.jpg", "imageUrl": "https://obs.test/a.jpg"}],
        },
        headers={"X-API-Key": "test-integration-key"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 429
    assert response.headers["retry-after"] == "300"
    assert response.headers["x-pipeline-queue-depth"] == "100"
    assert staged is False


def test_customer_legacy_submit_path_accepts_same_url_contract(monkeypatch) -> None:
    storage = FakeStorageProvider()
    jobs = FakeJobService()

    async def fake_stage(images, *, storage, settings):
        return [StagedIntegrationImage("uploads/a.jpg", images[0].object_key)]

    monkeypatch.setattr("src.api.integration.stage_integration_urls", fake_stage)
    app.dependency_overrides[get_storage_provider] = lambda: storage
    app.dependency_overrides[get_job_service] = lambda: jobs
    app.dependency_overrides[get_settings] = lambda: Settings(
        integration_api_key="test-integration-key"
    )
    client = TestClient(app)

    response = client.post(
        "/api/app/image/filter-requests",
        json={
            "notifyUrl": "https://client.test/callback",
            "images": [{"objectKey": "a.jpg", "imageUrl": "https://obs.test/a.jpg"}],
        },
        headers={"x-api-key": "test-integration-key"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 201
    assert response.json()["taskId"] == "job_integration"


def test_url_job_rejects_duplicate_customer_object_keys() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        integration_api_key="test-integration-key"
    )
    client = TestClient(app)
    response = client.post(
        "/api/v1/integration/jobs",
        json={
            "notifyUrl": "https://client.test/callback",
            "images": [
                {"objectKey": "same.jpg", "imageUrl": "https://obs.test/a.jpg"},
                {"objectKey": "same.jpg", "imageUrl": "https://obs.test/b.jpg"},
            ],
        },
        headers={"X-API-Key": "test-integration-key"},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 422


def test_integration_job_progress_and_results_are_available() -> None:
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_storage_provider] = lambda: FakeStorageProvider()
    app.dependency_overrides[get_settings] = lambda: Settings(
        integration_api_key="test-integration-key",
        s3_presign_expires_seconds=600,
    )
    client = TestClient(app)

    progress = client.get(
        "/api/v1/integration/jobs/job_integration",
        headers={"X-API-Key": "test-integration-key"},
    )
    results = client.get(
        "/api/v1/integration/jobs/job_integration/results?limit=25&offset=0",
        headers={"X-API-Key": "test-integration-key"},
    )

    app.dependency_overrides.clear()

    assert progress.status_code == 200
    assert progress.json()["job_id"] == "job_integration"
    assert results.status_code == 200
    assert results.json()["download_expires_in"] == 600
    assert results.json()["images"][0]["original_url"].startswith("https://storage.test/")
    assert results.json()["images"][0]["enhanced_url"].startswith("https://storage.test/")


def test_integration_api_does_not_accept_internal_admin_key() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        api_key="internal-admin-key",
        integration_api_key="company-key",
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/integration/jobs/job_integration",
        headers={"X-API-Key": "internal-admin-key"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 401
