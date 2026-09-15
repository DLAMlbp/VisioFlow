from types import SimpleNamespace
from typing import ClassVar

import pytest

from src.services.images.beautify_planning import (
    BeautifyDecision,
    BeautifyPlanOutcome,
)
from src.services.profiles import BeautifyProfile
from src.workers import beautify_plan


class _SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


def _decision() -> BeautifyDecision:
    return BeautifyDecision.model_validate(
        {
            "needed": True,
            "reason": "画面略暗",
            "confidence": 0.9,
            "parameters": {
                "brightness": 1.05,
                "contrast": 1.02,
                "color": 1.0,
                "sharpness": 1.0,
                "auto_white_balance": True,
                "white_balance_strength": 0.2,
                "shadow_lift": 0.1,
                "highlight_recovery": 0.1,
                "denoise_strength": 0.1,
                "local_tone_strength": 0.1,
                "local_tone_clip_limit": 1.5,
                "glare_reduction_strength": 0.1,
                "local_clarity_strength": 0.1,
                "auto_straighten": False,
                "max_straighten_degrees": 3.0,
            },
            "parameter_reasons": {},
            "risk_flags": [],
        }
    )


def _profile() -> BeautifyProfile:
    return BeautifyProfile(
        id="test",
        version=1,
        description="自然美化",
        brightness=1.3,
        contrast=1.2,
        color=1.1,
        sharpness=1.0,
        auto_white_balance=True,
        denoise_strength=0.3,
        jpeg_quality=90,
    )


@pytest.mark.asyncio
async def test_worker_persists_each_batch_outcome_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [
        SimpleNamespace(
            id="img_ok",
            job_id="job_test",
            object_key="uploads/ok.jpg",
            processing_object_key="processing/ok.jpg",
            width=1200,
            height=800,
            metric=None,
        ),
        SimpleNamespace(
            id="img_failed",
            job_id="job_test",
            object_key="uploads/failed.jpg",
            processing_object_key="processing/failed.jpg",
            width=800,
            height=1200,
            metric=None,
        ),
    ]

    class Repository:
        saved: ClassVar[list[tuple[str, str]]] = []
        failed: ClassVar[list[str]] = []

        def __init__(self, _session):
            pass

        async def claim_beautify_plan_batch(self, image_id: str, *, limit: int):
            assert image_id == "img_ok"
            assert limit == 4
            return items

        async def get_config(self, job_id: str):
            assert job_id == "job_test"
            return SimpleNamespace(
                cancel_requested_at=None,
                beautify_profile_snapshot=None,
                beautify_profile_id="test",
            )

        async def save_beautify_plan(self, image_id: str, *, status: str, **_values):
            type(self).saved.append((image_id, status))
            return status == "completed"

        async def fail_item(self, item, *_args, **_kwargs):
            type(self).failed.append(item.id)

    class Storage:
        async def download(self, object_key: str):
            return object_key.encode()

    class PlanningService:
        def __init__(self, settings):
            assert settings.ai_tagging_model == "gpt-5.6-luna"

        async def analyze_many(self, inputs, *, fairness_key: str):
            assert len(inputs) == 2
            assert fairness_key == "job_test"
            return [
                BeautifyPlanOutcome(status="completed", payload=_decision(), duration_ms=900),
                BeautifyPlanOutcome(
                    status="failed",
                    error_message="上游暂不可用",
                    duration_ms=1200,
                    retryable=True,
                ),
            ]

    Repository.saved = []
    Repository.failed = []
    published: list[str] = []
    monkeypatch.setattr(beautify_plan, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(beautify_plan, "ImageJobRepository", Repository)
    monkeypatch.setattr(
        beautify_plan,
        "get_settings",
        lambda: SimpleNamespace(
            post_filter_beautify_plan_enabled=True,
            ai_beautify_batch_size=4,
        ),
    )
    monkeypatch.setattr(
        beautify_plan,
        "load_ai_model_settings",
        lambda _settings: SimpleNamespace(ai_tagging_model="gpt-5.6-luna"),
    )
    monkeypatch.setattr(beautify_plan, "beautify_from_snapshot", lambda *_args: _profile())
    monkeypatch.setattr(beautify_plan, "get_storage_provider", lambda: Storage())
    monkeypatch.setattr(beautify_plan, "BeautifyPlanningService", PlanningService)
    monkeypatch.setattr(
        beautify_plan.RedactionDetectionTaskPublisher,
        "publish",
        lambda _publisher, image_id: published.append(image_id),
    )

    await beautify_plan._plan_beautify("img_ok")

    assert Repository.saved == [("img_ok", "completed"), ("img_failed", "failed")]
    assert Repository.failed == ["img_failed"]
    assert published == ["img_ok"]


@pytest.mark.asyncio
async def test_worker_reuses_combined_plan_without_download_or_ai_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire_decision = _decision().model_dump(mode="json")
    wire_decision["parameter_reasons"] = [
        {"name": "brightness", "reason": "画面略暗"}
    ]
    item = SimpleNamespace(
        id="img_reused",
        job_id="job_test",
        ai_processing_json={"beautify_plan": wire_decision},
        ai_processing_model="gpt-5.6-luna",
        ai_processing_prompt_version="combined-v18",
    )

    class Repository:
        saved: ClassVar[dict[str, object] | None] = None

        def __init__(self, _session):
            pass

        async def claim_beautify_plan_batch(self, image_id: str, *, limit: int):
            assert image_id == item.id
            assert limit == 4
            return [item]

        async def get_config(self, job_id: str):
            assert job_id == "job_test"
            return SimpleNamespace(
                cancel_requested_at=None,
                beautify_profile_snapshot=None,
                beautify_profile_id="test",
            )

        async def save_beautify_plan(self, image_id: str, **values):
            assert image_id == item.id
            type(self).saved = values
            return True

    class Storage:
        async def download(self, _object_key: str):
            raise AssertionError("reused plan must not download the image")

    class PlanningService:
        def __init__(self, _settings):
            raise AssertionError("reused plan must not issue another AI request")

    Repository.saved = None
    published: list[str] = []
    monkeypatch.setattr(beautify_plan, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(beautify_plan, "ImageJobRepository", Repository)
    monkeypatch.setattr(
        beautify_plan,
        "get_settings",
        lambda: SimpleNamespace(
            post_filter_beautify_plan_enabled=True,
            ai_beautify_batch_size=4,
        ),
    )
    monkeypatch.setattr(
        beautify_plan,
        "load_ai_model_settings",
        lambda _settings: SimpleNamespace(ai_tagging_model="gpt-5.6-luna"),
    )
    monkeypatch.setattr(beautify_plan, "beautify_from_snapshot", lambda *_args: _profile())
    monkeypatch.setattr(beautify_plan, "get_storage_provider", lambda: Storage())
    monkeypatch.setattr(beautify_plan, "BeautifyPlanningService", PlanningService)
    monkeypatch.setattr(
        beautify_plan.RedactionDetectionTaskPublisher,
        "publish",
        lambda _publisher, image_id: published.append(image_id),
    )

    await beautify_plan._plan_beautify(item.id)

    assert Repository.saved is not None
    assert Repository.saved["status"] == "completed"
    assert Repository.saved["model_name"] == "gpt-5.6-luna"
    assert Repository.saved["prompt_version"] == "combined-v18"
    assert Repository.saved["duration_ms"] == 0
    assert published == [item.id]
