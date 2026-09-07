from datetime import UTC, datetime

import pytest

from src.core.config import Settings
from src.models.image_item import ImageItem
from src.models.image_job import ImageJob
from src.models.image_result import ImageResult
from src.repositories.jobs import CallbackJob, JobProgressSnapshot
from src.services.jobs.callback_security import (
    CallbackConfigurationError,
    validate_callback_destination,
)
from src.services.jobs.callbacks import (
    CallbackDeliveryError,
    CustomerCallbackPayload,
    CustomerCallbackResult,
    ImageJobCallbackPayload,
    build_job_callback_payload,
    post_job_callback,
)
from src.services.storage.interfaces import StorageProvider


class FakeCallbackRepository:
    def __init__(self, job: ImageJob) -> None:
        self.job = job

    async def get_progress_snapshot(self, job_id: str) -> JobProgressSnapshot | None:
        if job_id != self.job.id:
            return None
        return JobProgressSnapshot(
            id=self.job.id,
            status=self.job.status,
            total_count=self.job.total_count,
            processed_count=self.job.processed_count,
            selected_count=self.job.selected_count,
            rejected_count=self.job.rejected_count,
            not_selected_count=self.job.not_selected_count,
            stage_counts={"completed": self.job.selected_count},
        )

    async def list_result_items(self, job_id, *, limit, offset, decision):
        items = [item for item in self.job.items if item.result is not None]
        return len(items), items[offset : offset + limit]


class FakeCallbackStorage(StorageProvider):
    async def upload(self, object_key: str, data: bytes, content_type: str) -> None:
        raise NotImplementedError

    async def download(self, object_key: str) -> bytes:
        raise NotImplementedError

    async def get_size(self, object_key: str) -> int:
        raise NotImplementedError

    async def delete(self, object_key: str) -> None:
        raise NotImplementedError

    async def presign_upload(self, object_key: str, content_type: str, expires_seconds: int) -> str:
        raise NotImplementedError

    async def presign_download(self, object_key: str, expires_seconds: int) -> str:
        return f"https://storage.test/{object_key}?expires={expires_seconds}"


@pytest.mark.asyncio
async def test_callback_payload_contains_terminal_status_and_download_urls() -> None:
    completed_at = datetime(2026, 8, 29, 8, 30, tzinfo=UTC)
    item = ImageItem(
        id="img_callback",
        job_id="job_callback",
        object_key="uploads/source.jpg",
        status="selected",
    )
    item.result = ImageResult(
        id="res_callback",
        image_id=item.id,
        decision="selected",
        final_score=96.5,
        enhanced_object_key="enhanced/result.jpg",
        reject_codes_json=[],
        reasons_json=["处理完成"],
    )
    job = ImageJob(
        id="job_callback",
        status="completed",
        filter_profile_id="flt_user",
        beautify_profile_id="bty_user",
        similarity_profile_id="library_similarity_v2",
        enhance_level=1,
        max_selected=1,
        total_count=1,
        processed_count=1,
        selected_count=1,
        rejected_count=0,
        not_selected_count=0,
        completed_at=completed_at,
        items=[item],
    )
    callback_job = CallbackJob(
        id=job.id,
        callback_url="https://client.test/callback",
        status=job.status,
        completed_at=completed_at,
        attempts=1,
    )

    payload = await build_job_callback_payload(
        callback_job,
        FakeCallbackRepository(job),
        Settings(_env_file=None, s3_presign_expires_seconds=600),
        FakeCallbackStorage(),
    )

    assert payload.event == "image.job.finished"
    assert payload.event_id == "job_callback:2026-08-29T08:30:00+00:00"
    assert payload.status == "completed"
    assert payload.images[0].original_url.startswith("https://storage.test/")
    assert payload.images[0].enhanced_url.startswith("https://storage.test/")


@pytest.mark.asyncio
async def test_customer_callback_uses_object_key_and_customer_field_names() -> None:
    completed_at = datetime(2026, 8, 29, 8, 30, tzinfo=UTC)
    item = ImageItem(
        id="img_customer",
        job_id="job_customer",
        object_key="uploads/internal.jpg",
        client_object_key="img/2026/08/customer.jpg",
        status="selected",
    )
    item.result = ImageResult(
        id="res_customer",
        image_id=item.id,
        decision="selected",
        final_score=86.5,
        enhanced_object_key="enhanced/customer.jpg",
        reject_codes_json=[],
        reasons_json=["处理完成"],
    )
    job = ImageJob(
        id="job_customer",
        status="completed",
        filter_profile_id="completion_routing_v1",
        beautify_profile_id="integration_natural_v1",
        similarity_profile_id="library_similarity_v2",
        enhance_level=1,
        max_selected=1,
        total_count=1,
        processed_count=1,
        selected_count=1,
        rejected_count=0,
        not_selected_count=0,
        completed_at=completed_at,
        items=[item],
    )
    callback_job = CallbackJob(
        id=job.id,
        callback_url="https://client.test/callback",
        status=job.status,
        completed_at=completed_at,
        attempts=1,
        callback_contract="customer_v1",
    )

    payload = await build_job_callback_payload(
        callback_job,
        FakeCallbackRepository(job),
        Settings(_env_file=None, s3_presign_expires_seconds=600),
        FakeCallbackStorage(),
    )

    assert isinstance(payload, CustomerCallbackPayload)
    body = payload.model_dump(mode="json", by_alias=True)
    assert "event_id" not in body
    assert body == {
        "results": [
            {
                "objectKey": "img/2026/08/customer.jpg",
                "decision": "selected",
                "score": 86.5,
                "enhancedUrl": "https://storage.test/enhanced/customer.jpg?expires=600",
                "enhancedMd5": None,
                "aiTags": [],
            }
        ],
        "errorMessage": "",
    }


def test_customer_callback_contract_field_names_are_exactly_stable() -> None:
    assert set(CustomerCallbackPayload.model_fields) == {
        "event_id",
        "event",
        "results",
        "error_message",
        "failure",
    }
    assert set(CustomerCallbackResult.model_fields) == {
        "object_key",
        "decision",
        "score",
        "enhanced_url",
        "enhanced_md5",
        "ai_tags",
    }


@pytest.mark.asyncio
async def test_failed_customer_callback_contains_structured_failure() -> None:
    completed_at = datetime(2026, 9, 3, 9, 10, tzinfo=UTC)
    job = ImageJob(
        id="job_failed",
        status="failed",
        filter_profile_id="global_filter_v1",
        beautify_profile_id="integration_natural_v1",
        similarity_profile_id="library_similarity_v2",
        enhance_level=1,
        max_selected=1,
        total_count=1,
        processed_count=1,
        selected_count=0,
        rejected_count=0,
        not_selected_count=0,
        completed_at=completed_at,
        items=[],
    )
    callback_job = CallbackJob(
        id=job.id,
        callback_url="https://client.test/callback",
        status="failed",
        completed_at=completed_at,
        attempts=1,
        callback_contract="customer_v1",
        failed_node="classification",
        failure_code="UPSTREAM_HTTP_ERROR",
        failure_message="节点 classification 执行失败：HTTP 404",
        failed_image_id="img_failed",
        failure_duration_ms=1250,
        upstream_status_code=404,
        failed_at=completed_at,
    )

    payload = await build_job_callback_payload(
        callback_job,
        FakeCallbackRepository(job),
        Settings(_env_file=None),
        FakeCallbackStorage(),
    )
    body = payload.model_dump(mode="json", by_alias=True)

    assert body["errorMessage"] == "节点 classification 执行失败：HTTP 404"
    assert body["failure"] == {
        "node": "classification",
        "code": "UPSTREAM_HTTP_ERROR",
        "message": "节点 classification 执行失败：HTTP 404",
        "imageId": "img_failed",
        "durationMs": 1250,
        "upstreamStatusCode": 404,
        "failedAt": "2026-09-03T09:10:00Z",
    }


@pytest.mark.asyncio
async def test_post_callback_accepts_any_2xx_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def getcode(self):
            return 204

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("src.services.jobs.callbacks.urllib.request.urlopen", fake_urlopen)
    payload = ImageJobCallbackPayload(
        event_id="job_test:1",
        job_id="job_test",
        status="completed",
        completed_at=datetime(2026, 8, 29, tzinfo=UTC),
        total=0,
        selected=0,
        rejected=0,
        download_expires_in=900,
        images=[],
    )

    await post_job_callback(
        "https://client.test/callback",
        payload,
        timeout_seconds=15,
        signing_secret="test-callback-signing-secret",
        allowed_hosts="client.test",
    )

    assert captured["timeout"] == 15
    assert captured["request"].get_header("X-callback-id") == "job_test:1"
    assert captured["request"].get_header("X-callback-timestamp")
    assert captured["request"].get_header("X-callback-signature").startswith("sha256=")


@pytest.mark.asyncio
async def test_post_callback_rejects_non_http_url() -> None:
    payload = ImageJobCallbackPayload(
        event_id="job_test:1",
        job_id="job_test",
        status="completed",
        completed_at=datetime(2026, 8, 29, tzinfo=UTC),
        total=0,
        selected=0,
        rejected=0,
        download_expires_in=900,
        images=[],
    )

    with pytest.raises(CallbackDeliveryError, match="http/https"):
        await post_job_callback("file:///tmp/callback", payload, timeout_seconds=15)


@pytest.mark.asyncio
async def test_post_callback_rejects_host_outside_allowlist() -> None:
    payload = ImageJobCallbackPayload(
        event_id="job_test:1",
        job_id="job_test",
        status="completed",
        completed_at=datetime(2026, 8, 29, tzinfo=UTC),
        total=0,
        selected=0,
        rejected=0,
        download_expires_in=900,
        images=[],
    )

    with pytest.raises(CallbackDeliveryError, match="允许列表"):
        await post_job_callback(
            "https://untrusted.test/callback",
            payload,
            timeout_seconds=15,
            allowed_hosts="client.test",
        )


def test_production_callback_requires_https() -> None:
    with pytest.raises(CallbackConfigurationError, match="HTTPS"):
        validate_callback_destination(
            "http://client.test/callback",
            production=True,
            allowed_hosts="client.test",
        )
