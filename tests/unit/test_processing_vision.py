import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.services.images.processing_vision import (
    BeautifyDecision,
    ProcessingVisionPayload,
    ProcessingVisionService,
    beautify_from_processing_json,
    content_from_processing_json,
    merge_beautify_plan,
)
from src.services.managed_profiles import ManagedProfileService
from src.services.profiles import BeautifyProfile


def _profile() -> BeautifyProfile:
    return BeautifyProfile(
        id="test",
        version=1,
        description="自然美化",
        brightness=1.3,
        contrast=1.2,
        color=1.1,
        sharpness=1,
        auto_white_balance=True,
        denoise_strength=0.3,
        jpeg_quality=90,
    )


def _parameters(*, brightness: float = 1.05) -> dict[str, object]:
    return {
        "brightness": brightness,
        "contrast": 1.02,
        "color": 1.0,
        "auto_white_balance": True,
        "white_balance_strength": 0.4,
        "shadow_lift": 0.12,
        "highlight_recovery": 0.08,
        "denoise_strength": 0.1,
        "local_tone_strength": 0.12,
        "local_tone_clip_limit": 1.4,
        "glare_reduction_strength": 0.1,
        "local_clarity_strength": 0.16,
        "auto_straighten": False,
        "max_straighten_degrees": 3.0,
    }


def _payload(*, decision: str = "pass", brightness: float = 1.05) -> dict[str, object]:
    return {
        "filter": {
            "decision": decision,
            "reason": "符合客户填写的过滤要求",
            "confidence": 0.92,
            "dimensions": [
                {
                    "dimension": "画面清晰度",
                    "passed": decision == "pass",
                    "reason": "主体和装修细节可辨认",
                }
            ],
        },
        "beautify": {
            "needed": True,
            "reason": "画面略暗",
            "parameters": _parameters(brightness=brightness),
        },
        "content": {
            "summary": "施工中的厨房",
            "scene": "住宅室内",
            "space": "厨房",
            "condition": "施工中",
            "content_type": "环境展示",
            "subjects": ["墙砖"],
            "view": "空间全景",
            "tags": [],
            "categories": {},
            "candidate_tags": [],
            "confidence": 0.9,
            "risks": [],
        },
    }


@pytest.mark.asyncio
async def test_combined_processing_response_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ProcessingVisionService(
        Settings(ai_tagging_api_key="test-key", ai_tagging_model="gpt-5.6-sol")
    )
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [{"message": {"content": json.dumps(_payload(), ensure_ascii=False)}}]
        },
    )

    outcome = await service.analyze(
        b"image",
        filter_instruction="只保留施工现场",
        beautify_instruction="自然提亮暗部",
    )

    assert outcome.status == "completed"
    assert outcome.payload is not None
    assert outcome.payload.filter.decision == "pass"
    assert outcome.payload.content.space == "厨房"


@pytest.mark.asyncio
async def test_invalid_ai_parameter_falls_back_as_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ProcessingVisionService(Settings(ai_tagging_api_key="test-key"))
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [
                {"message": {"content": json.dumps(_payload(brightness=3), ensure_ascii=False)}}
            ]
        },
    )

    outcome = await service.analyze(
        b"image",
        filter_instruction="保留有效图片",
        beautify_instruction="自然美化",
    )

    assert outcome.status == "failed"
    assert outcome.payload is None
    assert outcome.error_message is not None
    assert "beautify.parameters.brightness" in outcome.error_message


def test_ai_filter_reject_and_beautify_plan_are_strict_and_bounded() -> None:
    payload = ProcessingVisionPayload.model_validate(_payload(decision="reject"))
    assert payload.filter.decision == "reject"
    assert payload.filter.rejected is True

    with pytest.raises(ValidationError):
        ProcessingVisionPayload.model_validate(_payload(decision="review"))

    merged = merge_beautify_plan(_profile(), payload.beautify)
    assert merged.brightness == 1.05
    assert merged.contrast == 1.02
    assert merged.shadow_lift == 0.12
    assert merged.denoise_strength == 0.1
    assert merged.auto_straighten is False
    assert merged.jpeg_quality == 90


def test_any_failed_dimension_rejects_even_when_model_summary_says_pass() -> None:
    payload = ProcessingVisionPayload.model_validate(_payload(decision="pass"))
    payload.filter.dimensions[0].passed = False

    assert payload.filter.rejected is True


def test_processing_json_helpers_support_reuse_and_old_jobs() -> None:
    payload = _payload()
    assert content_from_processing_json(payload).summary == "施工中的厨房"
    assert beautify_from_processing_json(payload).needed is True
    assert content_from_processing_json(None) is None
    assert beautify_from_processing_json({"legacy": True}) is None
    payload["beautify"]["parameters"] = {}
    assert content_from_processing_json(payload).summary == "施工中的厨房"
    assert beautify_from_processing_json(payload).parameters.brightness == 1.0


def test_missing_beautify_parameters_use_safe_neutral_values() -> None:
    decision = BeautifyDecision(needed=False, reason="无需调整", parameters={})

    assert decision.parameters.brightness == 1.0
    assert decision.parameters.auto_white_balance is False


@pytest.mark.asyncio
async def test_markdown_fenced_json_and_extra_fields_are_tolerated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProcessingVisionService(Settings(ai_tagging_api_key="test-key"))
    payload = _payload()
    payload["provider_note"] = "ignored"
    content = f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {"choices": [{"message": {"content": content}}]},
    )

    outcome = await service.analyze(b"image", filter_instruction="保留有效图片")

    assert outcome.status == "completed"


def test_managed_profile_snapshot_contains_original_instruction() -> None:
    row = SimpleNamespace(
        id="flt_test",
        name="施工图过滤",
        version=3,
        instruction="只保留施工现场照片",
        config_json={"id": "flt_test"},
    )

    snapshot = ManagedProfileService._snapshot(row)

    assert snapshot["instruction"] == "只保留施工现场照片"
