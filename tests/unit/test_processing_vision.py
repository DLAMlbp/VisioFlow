import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.services.images import processing_vision as processing_vision_module
from src.services.images.beautify_planning import (
    BeautifyDecision,
    BeautifyPlanningService,
    beautify_plan_from_json,
    build_stored_plan,
)
from src.services.images.processing_vision import (
    FilterDecision,
    ProcessingVisionPayload,
    ProcessingVisionService,
    _strict_processing_response_format,
    compatibility_route_label,
    content_from_processing_json,
    precise_filter_reason,
)
from src.services.managed_profiles import ManagedProfileService
from src.services.profiles import BeautifyProfile, ProcessingStandard


def _profile() -> BeautifyProfile:
    return BeautifyProfile(
        id="test", version=1, description="自然美化", brightness=1.3,
        contrast=1.2, color=1.1, sharpness=1, auto_white_balance=True,
        denoise_strength=0.3, jpeg_quality=90,
    )


def _filter_payload(*, decision: str = "pass") -> dict[str, object]:
    return {"filter": {"decision": decision, "reason": "符合过滤要求", "confidence": 0.92,
        "dimensions": [{"dimension": "画面清晰度", "passed": decision == "pass", "reason": "主体可辨认"}]}}


def _beautify_payload(*, brightness: float = 1.05) -> dict[str, object]:
    return {
        "needed": True, "reason": "画面略暗", "confidence": 0.91,
        "parameters": {
            "brightness": brightness, "contrast": 1.02, "color": 1.0,
            "sharpness": 1.1, "auto_white_balance": True,
            "white_balance_strength": 0.4, "shadow_lift": 0.12,
            "highlight_recovery": 0.08, "denoise_strength": 0.1,
            "local_tone_strength": 0.12, "local_tone_clip_limit": 1.4,
            "glare_reduction_strength": 0.1, "local_clarity_strength": 0.16,
            "auto_straighten": False, "max_straighten_degrees": 3.0,
        },
        "parameter_reasons": {}, "risk_flags": [],
    }


@pytest.mark.asyncio
async def test_filter_response_is_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ProcessingVisionService(Settings(ai_tagging_enabled=True, ai_tagging_api_key="test-key"))
    monkeypatch.setattr(service, "_request", lambda *_args: {
        "choices": [{"message": {"content": json.dumps(_filter_payload(), ensure_ascii=False)}}]})
    outcome = await service.analyze(b"image", filter_instruction="只保留施工现场")
    assert outcome.status == "completed"
    assert outcome.payload.filter.decision == "pass"


@pytest.mark.asyncio
async def test_filter_response_rejects_content_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ProcessingVisionService(Settings(ai_tagging_enabled=True, ai_tagging_api_key="test-key"))
    payload = {**_filter_payload(), "content": {"tags": ["模型标签"]}}
    monkeypatch.setattr(service, "_request", lambda *_args: {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})
    outcome = await service.analyze(b"image", filter_instruction="保留有效图片")
    assert outcome.status == "failed"
    assert "content" in outcome.error_message


def test_routed_filter_strict_schema_requires_complete_contract() -> None:
    response_format = _strict_processing_response_format()
    json_schema = response_format["json_schema"]
    schema = json_schema["schema"]

    assert response_format["type"] == "json_schema"
    assert json_schema["strict"] is True
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"standard_selection", "filter"}
    for definition in schema["$defs"].values():
        if "properties" in definition:
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])


@pytest.mark.asyncio
async def test_filter_schema_failure_is_repaired_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProcessingVisionService(
        Settings(
            ai_tagging_enabled=True,
            ai_tagging_api_key="test-key",
            ai_processing_schema_max_retries=1,
        )
    )
    responses = iter(
        [
            {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]},
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(_filter_payload(), ensure_ascii=False)
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        ]
    )
    repair_contexts: list[dict[str, str] | None] = []
    schema_retry_calls = 0

    def fake_request(*args):
        repair_contexts.append(args[-1])
        return next(responses)

    async def before_schema_retry() -> None:
        nonlocal schema_retry_calls
        schema_retry_calls += 1

    monkeypatch.setattr(service, "_request", fake_request)

    outcome = await service.analyze(
        b"image",
        filter_instruction="保留有效图片",
        before_schema_retry=before_schema_retry,
    )

    assert outcome.status == "completed"
    assert outcome.payload is not None
    assert outcome.payload.filter.decision == "pass"
    assert schema_retry_calls == 1
    assert repair_contexts[0] is None
    assert "filter" in repair_contexts[1]["error_message"]
    assert outcome.diagnostic_json["attempts"] == 2
    assert outcome.diagnostic_json["recovered"] is True


@pytest.mark.asyncio
async def test_filter_schema_failure_stores_redacted_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProcessingVisionService(
        Settings(
            ai_tagging_enabled=True,
            ai_tagging_api_key="test-key",
            ai_processing_schema_max_retries=1,
        )
    )
    invalid_content = json.dumps(
        {"note": "Bearer secret-token data:image/png;base64,QUJDRA=="}
    )
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [
                {"message": {"content": invalid_content}, "finish_reason": "stop"}
            ]
        },
    )

    outcome = await service.analyze(b"image", filter_instruction="保留有效图片")

    assert outcome.status == "failed"
    assert "已自动纠错重试1次" in outcome.error_message
    assert outcome.diagnostic_json["attempts"] == 2
    assert outcome.diagnostic_json["recovered"] is False
    diagnostics = json.dumps(outcome.diagnostic_json, ensure_ascii=False)
    assert "secret-token" not in diagnostics
    assert "QUJDRA==" not in diagnostics
    assert "[redacted-image-data]" in diagnostics


def test_strict_schema_unsupported_falls_back_to_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProcessingVisionService(
        Settings(ai_tagging_enabled=True, ai_tagging_api_key="test-key")
    )
    request_formats: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"choices": []}).encode()

    def fake_urlopen(request, *, timeout):
        del timeout
        body = json.loads(request.data.decode())
        request_formats.append(body["response_format"]["type"])
        if len(request_formats) == 1:
            raise HTTPError(request.full_url, 400, "unsupported", None, None)
        return FakeResponse()

    monkeypatch.setattr(processing_vision_module, "_resize_for_tagging", lambda *_args: b"jpeg")
    monkeypatch.setattr(processing_vision_module, "urlopen", fake_urlopen)
    standard = ProcessingStandard(
        id="standard_test",
        version=1,
        description="测试标准",
        activation_rule="后端已经选定",
        filter_rule="保留有效图片",
    )

    response = service._request(b"image", [standard], "reject", {}, None)

    assert response == {"choices": []}
    assert request_formats == ["json_schema", "json_object"]


@pytest.mark.asyncio
async def test_beautify_planning_is_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    service = BeautifyPlanningService(Settings(ai_tagging_enabled=True, ai_tagging_api_key="test-key"))
    monkeypatch.setattr(service, "_request", lambda *_args: {
        "choices": [{"message": {"content": json.dumps(_beautify_payload(), ensure_ascii=False)}}]})
    outcome = await service.analyze(b"image", instruction="自然提亮")
    stored = build_stored_plan(_profile(), outcome.payload)
    assert stored.effective_parameters.brightness == 1.05
    assert beautify_plan_from_json(stored.model_dump()).decision.reason == "画面略暗"


@pytest.mark.asyncio
async def test_invalid_beautify_parameter_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    service = BeautifyPlanningService(Settings(ai_tagging_enabled=True, ai_tagging_api_key="test-key"))
    monkeypatch.setattr(service, "_request", lambda *_args: {
        "choices": [{"message": {"content": json.dumps(_beautify_payload(brightness=3))}}]})
    outcome = await service.analyze(b"image", instruction="自然美化")
    assert outcome.status == "failed"


def test_filter_summary_must_match_dimensions() -> None:
    payload = _filter_payload()
    payload["filter"]["dimensions"][0]["passed"] = False
    with pytest.raises(ValidationError, match="过滤汇总结论与审核维度不一致"):
        ProcessingVisionPayload.model_validate(payload)


def test_filter_requires_dimensions() -> None:
    payload = _filter_payload()
    payload["filter"]["dimensions"] = []
    with pytest.raises(ValidationError):
        ProcessingVisionPayload.model_validate(payload)


def test_precise_filter_reason_uses_failed_dimension_and_valid_context() -> None:
    decision = FilterDecision.model_validate({
        "decision": "reject",
        "reason": "图片总体不符合要求",
        "confidence": 0.98,
        "dimensions": [
            {
                "dimension": "构图、角度与空间感维度",
                "passed": False,
                "reason": "画面仅展示局部地面，缺少周边空间信息。",
            },
            {
                "dimension": "内容相关性维度",
                "passed": True,
                "reason": "空鼓锤和标注能够确认这是瓦工验收节点。",
            },
        ],
    })

    assert precise_filter_reason(
        decision,
        standard_name="非完工图片过滤",
    ) == (
        "按「非完工图片过滤」标准，未通过「构图、角度与空间感」要求："
        "画面仅展示局部地面，缺少周边空间信息。"
        "已识别的有效内容：空鼓锤和标注能够确认这是瓦工验收节点。"
    )


def test_builtin_standard_ids_restore_compatibility_route_labels() -> None:
    assert compatibility_route_label("standard_completed_v1") == "completed"
    assert compatibility_route_label("standard_non_completed_v1") == "non_completed"
    assert compatibility_route_label("custom_standard") is None


def test_legacy_content_helper_is_read_only_compatible() -> None:
    payload = {"content": {"summary": "施工中的厨房", "tags": [], "categories": {},
        "candidate_tags": [], "confidence": 0.9, "risks": []}}
    assert content_from_processing_json(payload).summary == "施工中的厨房"
    assert content_from_processing_json(_filter_payload()) is None


def test_missing_beautify_parameters_are_neutral() -> None:
    decision = BeautifyDecision(needed=False, reason="无需调整", parameters={})
    assert decision.parameters.brightness == 1.0


def test_managed_profile_snapshot_contains_instruction() -> None:
    row = SimpleNamespace(id="flt_test", name="施工图过滤", version=3,
        instruction="只保留施工现场照片", config_json={"id": "flt_test"})
    assert ManagedProfileService._snapshot(row)["instruction"] == "只保留施工现场照片"
