from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from src.api.jobs import get_job_service
from src.core.config import Settings, get_settings
from src.main import app
from src.schemas.jobs import (
    CreateImageJobRequest,
    ImageJobHistoryResponse,
    ImageJobProgressResponse,
    ImageJobResultsResponse,
    UpdateLogoRedactionResponse,
)
from src.services.jobs.service import SelectedImageDownload


class FakeJobService:
    def __init__(self) -> None:
        self.job_id = "job_test"

    async def create_job(self, payload: CreateImageJobRequest):
        return {
            "job_id": self.job_id,
            "status": "queued",
            "total": len(payload.images),
        }

    async def get_progress(self, job_id: str):
        return ImageJobProgressResponse(
            job_id=job_id,
            status="queued",
            progress=0,
            total=2,
            processed=0,
            selected=0,
            rejected=0,
        )

    async def get_results(
        self, job_id: str, *, limit: int = 50, offset: int = 0, decision: str | None = None
    ):
        return ImageJobResultsResponse(
            job_id=job_id,
            total=2,
            selected=1,
            rejected=1,
            result_total=0,
            limit=limit,
            offset=offset,
            images=[],
        )

    async def list_history(self, limit: int, offset: int):
        return ImageJobHistoryResponse(total=1, limit=limit, offset=offset, items=[])

    async def get_selected_downloads(self, job_id: str, image_ids: list[str] | None = None):
        downloads = [
            SelectedImageDownload(
                object_key="enhanced/job_test/image-1.jpg",
                archive_filename="001_living-room.jpg",
            ),
            SelectedImageDownload(
                object_key="enhanced/job_test/image-2.jpg",
                archive_filename="002_kitchen.jpg",
            ),
        ]
        if image_ids is None:
            return downloads
        return downloads[:1] if "img_1" in image_ids else []

    async def update_logo_redaction(
        self,
        job_id: str,
        image_id: str,
        boxes: list[tuple[int, int, int, int]],
    ) -> UpdateLogoRedactionResponse:
        del job_id
        return UpdateLogoRedactionResponse(
            image_id=image_id,
            status="manual_applied" if boxes else "manual_cleared",
            boxes=boxes,
            image_size=(1080, 1440),
            detections=len(boxes),
        )


class NotFoundJobService(FakeJobService):
    async def get_progress(self, job_id: str):
        from src.services.jobs.service import JobNotFound

        raise JobNotFound("Job 不存在")


def test_create_image_job_returns_201_and_job_id() -> None:
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/image/jobs",
        json={
            "filter_route": {
                "completion_profile": "completion_renovation_v1",
                "completed_filter_profile": "standard_completed_v1",
                "non_completed_filter_profile": "standard_non_completed_v1",
            },
            "beautify_profile": "natural_v1",
            "enhance_level": 1,
            "max_selected": 10,
            "images": [
                {"object_key": "uploads/2026/08/19/a.jpg"},
                {"object_key": "uploads/2026/08/19/b.jpg"},
            ],
        },
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json() == {"job_id": "job_test", "status": "queued", "total": 2}


def test_create_image_job_rejects_empty_images() -> None:
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/image/jobs",
        json={
            "processing_standards": ["std_finished", "std_unfinished"],
            "beautify_profile": "natural_v1",
            "enhance_level": 1,
            "max_selected": 10,
            "images": [],
        },
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 422


def test_update_logo_redaction_accepts_final_operator_boxes() -> None:
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.put(
        "/api/v1/image/jobs/job_test/images/img_1/redaction/logos",
        json={"boxes": [[20, 30, 220, 130], [400, 500, 600, 620]]},
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["status"] == "manual_applied"
    assert response.json()["detections"] == 2


def test_update_logo_redaction_rejects_empty_or_negative_box_geometry() -> None:
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.put(
        "/api/v1/image/jobs/job_test/images/img_1/redaction/logos",
        json={"boxes": [[20, 30, 20, 130], [-1, 0, 10, 10]]},
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 422


def test_list_image_job_history_returns_summaries() -> None:
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.get("/api/v1/image/jobs?limit=10", headers={"X-API-Key": "test-api-key"})

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"total": 1, "limit": 10, "offset": 0, "items": []}


def test_get_image_job_returns_progress() -> None:
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.get("/api/v1/image/jobs/job_test", headers={"X-API-Key": "test-api-key"})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["job_id"] == "job_test"
    assert response.json()["status"] == "queued"
    assert response.json()["progress"] == 0


def test_get_image_job_returns_404_for_missing_job() -> None:
    app.dependency_overrides[get_job_service] = lambda: NotFoundJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.get("/api/v1/image/jobs/job_missing", headers={"X-API-Key": "test-api-key"})

    app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Job 不存在"


def test_get_image_job_results_returns_final_summary() -> None:
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.get(
        "/api/v1/image/jobs/job_test/results",
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["selected"] == 1
    assert response.json()["rejected"] == 1


def test_download_selected_images_returns_one_zip_with_enhanced_files(
    monkeypatch,
) -> None:
    class FakeStorage:
        def __init__(self) -> None:
            self.downloaded_keys: list[str] = []

        async def download(self, object_key: str) -> bytes:
            self.downloaded_keys.append(object_key)
            return {"enhanced/job_test/image-1.jpg": b"enhanced-one"}[object_key]

    storage = FakeStorage()
    monkeypatch.setattr("src.api.jobs.get_storage_provider", lambda: storage)
    app.dependency_overrides[get_job_service] = lambda: FakeJobService()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/image/jobs/job_test/downloads/selected",
        json={"image_ids": ["img_1", "rejected-image"]},
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert storage.downloaded_keys == ["enhanced/job_test/image-1.jpg"]
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["001_living-room.jpg"]
        assert archive.read("001_living-room.jpg") == b"enhanced-one"


def test_download_selected_images_rejects_empty_archive() -> None:
    class EmptyDownloadService(FakeJobService):
        async def get_selected_downloads(self, job_id: str, image_ids: list[str] | None = None):
            return []

    app.dependency_overrides[get_job_service] = lambda: EmptyDownloadService()
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-api-key")
    client = TestClient(app)

    response = client.post(
        "/api/v1/image/jobs/job_test/downloads/selected",
        json={},
        headers={"X-API-Key": "test-api-key"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"] == "没有可下载的美化图片"
