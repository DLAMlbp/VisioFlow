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
    ActivationEvaluation,
    FilterDecision,
    ProcessingVisionPayload,
    ProcessingVisionService,
    StandardSelection,
    _normalize_standard_selection,
    _parse_processing_content,
    _strict_processing_response_format,
    _user_prompt,
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
    assert set(schema["required"]) == {
        "standard_selection",
        "filter",
        "redaction_analysis",
    }
    for definition in schema["$defs"].values():
        if "properties" in definition:
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])


def test_indexed_schema_limits_selection_to_current_candidate_range() -> None:
    response_format = _strict_processing_response_format(candidate_count=11)
    schema = response_format["json_schema"]["schema"]
    index_schema = schema["$defs"]["CandidateStandardSelection"]["properties"][
        "selected_candidate_index"
    ]

    assert response_format["json_schema"]["name"] == "indexed_routed_filter_response"
    assert index_schema["type"] == "integer"
    assert index_schema["minimum"] == 0
    assert index_schema["maximum"] == 10
    assert index_schema["enum"] == list(range(11))
    assert "standard_id" not in json.dumps(schema)


def test_indexed_prompt_does_not_expose_business_standard_ids() -> None:
    standards = [
        ProcessingStandard(
            id="std_private_finished_uuid",
            name="完工图",
            version=1,
            description="完工图",
            classification_rule="硬装已经完成",
            filter_rule="审核清晰度",
        ),
        ProcessingStandard(
            id="std_private_fallback_uuid",
            name="其他装修图",
            version=1,
            description="其他装修图",
            classification_rule="其他装修场景",
            filter_rule="审核清晰度",
            is_fallback=True,
        ),
    ]

    prompt = _user_prompt(standards, indexed_selection=True)

    assert '"candidate_index": 0' in prompt
    assert '"candidate_index": 1' in prompt
    assert "selected_candidate_index" in prompt
    assert "std_private_finished_uuid" not in prompt
    assert "std_private_fallback_uuid" not in prompt
    assert "selected_standard_id" not in prompt


def test_indexed_batch_responses_map_to_real_ids_without_shared_state() -> None:
    standards = [
        ProcessingStandard(
            id=f"std_{index:02d}_opaque_identifier",
            name=f"分类 {index}",
            version=1,
            description=f"分类 {index}",
            classification_rule=f"命中分类 {index}",
            filter_rule="审核图片质量",
            is_fallback=index == 10,
        )
        for index in range(11)
    ]

    for image_index in range(500):
        candidate_index = image_index % len(standards)
        content = json.dumps(
            {
                "standard_selection": {
                    "selected_candidate_index": candidate_index,
                    "reason": f"命中候选 {candidate_index}",
                    "confidence": 0.95,
                },
                **_filter_payload(),
                "redaction_analysis": None,
            },
            ensure_ascii=False,
        )
        payload = _parse_processing_content(content, standards=standards)

        assert payload.standard_selection is not None
        assert payload.standard_selection.selected_standard_id == standards[candidate_index].id
        assert payload.standard_selection.evaluations[0].standard_id == standards[candidate_index].id


@pytest.mark.asyncio
async def test_out_of_range_candidate_fails_once_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProcessingVisionService(
        Settings(
            ai_tagging_enabled=True,
            ai_tagging_api_key="test-key",
            ai_global_scheduler_enabled=False,
            ai_processing_schema_max_retries=0,
        )
    )
    standard = ProcessingStandard(
        id="std_only",
        name="唯一标准",
        version=1,
        description="唯一标准",
        classification_rule="始终命中",
        filter_rule="审核图片质量",
    )
    calls = 0

    def fake_request(*_args):
        nonlocal calls
        calls += 1
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "standard_selection": {
                                    "selected_candidate_index": 1,
                                    "reason": "错误序号",
                                    "confidence": 0.9,
                                },
                                **_filter_payload(),
                                "redaction_analysis": None,
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(service, "_request", fake_request)

    outcome = await service.analyze(b"image", standards=[standard])

    assert outcome.status == "failed"
    assert outcome.payload is None
    assert "候选范围之外" in outcome.error_message
    assert calls == 1


@pytest.mark.asyncio
async def test_provider_timeout_is_explicit_upstream_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProcessingVisionService(
        Settings(
            _env_file=None,
            ai_tagging_enabled=True,
            ai_tagging_api_key="test-key",
            ai_global_scheduler_enabled=False,
        )
    )
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: (_ for _ in ()).throw(TimeoutError("provider timed out")),
    )

    outcome = await service.analyze(b"image", filter_instruction="保留有效图片")

    assert outcome.status == "failed"
    assert outcome.retryable is True
    assert outcome.failure_kind == "upstream_error"
    assert outcome.failure_code == "UPSTREAM_UNAVAILABLE"
    assert outcome.diagnostic_json["request_result"] == "error"
    assert outcome.diagnostic_json["request_error_type"] == "TimeoutError"
    assert outcome.diagnostic_json["validation_result"] == "not_run"


@pytest.mark.asyncio
async def test_missing_api_key_maps_to_configuration_code() -> None:
    service = ProcessingVisionService(
        Settings(_env_file=None, ai_tagging_enabled=True, ai_tagging_api_key="")
    )

    outcome = await service.analyze(b"image", filter_instruction="保留有效图片")

    assert outcome.failure_kind == "auth_config_error"
    assert outcome.failure_code == "AI_CONFIGURATION_ERROR"


def test_selection_safely_completes_only_an_omitted_fallback() -> None:
    explicit = ProcessingStandard(
        id="std_explicit",
        name="明确分类",
        version=1,
        description="明确分类",
        classification_rule="命中明确场景",
        filter_rule="审核画质",
    )
    fallback = ProcessingStandard(
        id="std_fallback",
        name="兜底分类",
        version=1,
        description="兜底分类",
        classification_rule="其他场景",
        filter_rule="拒绝未匹配图片",
        is_fallback=True,
    )
    payload = ProcessingVisionPayload(
        standard_selection=StandardSelection(
            evaluations=[
                ActivationEvaluation(
                    standard_id=explicit.id,
                    matched=True,
                    reason="画面直接命中明确分类",
                    confidence=0.96,
                )
            ],
            selected_standard_id=explicit.id,
            reason="唯一明确分类",
        ),
        filter=FilterDecision.model_validate(_filter_payload()["filter"]),
    )

    normalized = _normalize_standard_selection(payload, [explicit, fallback])

    assert normalized.standard_selection is not None
    assert [item.standard_id for item in normalized.standard_selection.evaluations] == [
        explicit.id,
        fallback.id,
    ]
    assert normalized.standard_selection.evaluations[1].matched is False


def test_selection_completes_an_omitted_nonselected_business_category() -> None:
    standards = [
        ProcessingStandard(
            id=standard_id,
            name=standard_id,
            version=1,
            description=standard_id,
            classification_rule=standard_id,
            filter_rule="审核画质",
            is_fallback=is_fallback,
        )
        for standard_id, is_fallback in (
            ("std_selected", False),
            ("std_omitted", False),
            ("std_fallback", True),
        )
    ]
    payload = ProcessingVisionPayload(
        standard_selection=StandardSelection(
            evaluations=[
                ActivationEvaluation(
                    standard_id="std_selected",
                    matched=True,
                    reason="命中",
                    confidence=0.9,
                ),
                ActivationEvaluation(
                    standard_id="std_fallback",
                    matched=False,
                    reason="不适用",
                    confidence=0.9,
                ),
            ],
            selected_standard_id="std_selected",
            reason="唯一明确分类",
        ),
        filter=FilterDecision.model_validate(_filter_payload()["filter"]),
    )

    normalized = _normalize_standard_selection(payload, standards)

    assert normalized.standard_selection is not None
    assert [item.standard_id for item in normalized.standard_selection.evaluations] == [
        "std_selected",
        "std_omitted",
        "std_fallback",
    ]
    assert normalized.standard_selection.evaluations[1].matched is False


def test_selection_uses_explicit_selected_id_to_resolve_redundant_matches() -> None:
    standards = [
        ProcessingStandard(
            id=standard_id,
            name=standard_id,
            version=1,
            description=standard_id,
            classification_rule=standard_id,
            filter_rule="审核画质",
            is_fallback=is_fallback,
        )
        for standard_id, is_fallback in (
            ("std_selected", False),
            ("std_other", False),
            ("std_fallback", True),
        )
    ]
    payload = ProcessingVisionPayload(
        standard_selection=StandardSelection(
            evaluations=[
                ActivationEvaluation(
                    standard_id="std_selected",
                    matched=True,
                    reason="最终选择",
                    confidence=0.92,
                ),
                ActivationEvaluation(
                    standard_id="std_other",
                    matched=True,
                    reason="存在部分重叠",
                    confidence=0.75,
                ),
                ActivationEvaluation(
                    standard_id="std_fallback",
                    matched=False,
                    reason="不适用",
                    confidence=0.9,
                ),
            ],
            selected_standard_id="std_selected",
            reason="最终唯一分类为 std_selected",
        ),
        filter=FilterDecision.model_validate(_filter_payload()["filter"]),
    )

    normalized = _normalize_standard_selection(payload, standards)

    assert normalized.standard_selection is not None
    assert [
        item.standard_id
        for item in normalized.standard_selection.evaluations
        if item.matched
    ] == ["std_selected"]


def test_selection_rebuilds_incomplete_evaluations_from_valid_selected_id() -> None:
    standards = [
        ProcessingStandard(
            id=standard_id,
            name=standard_id,
            version=1,
            description=standard_id,
            classification_rule=standard_id,
            filter_rule="审核画质",
            is_fallback=is_fallback,
        )
        for standard_id, is_fallback in (
            ("std_selected", False),
            ("std_other", False),
            ("std_fallback", True),
        )
    ]
    payload = ProcessingVisionPayload(
        standard_selection=StandardSelection(
            evaluations=[
                ActivationEvaluation(
                    standard_id="std_other",
                    matched=True,
                    reason="冗余判断一",
                    confidence=0.7,
                ),
                ActivationEvaluation(
                    standard_id="std_other",
                    matched=False,
                    reason="冗余判断二",
                    confidence=0.6,
                ),
                ActivationEvaluation(
                    standard_id="std_not_in_job",
                    matched=False,
                    reason="模型额外返回的无关标准",
                    confidence=0.5,
                ),
            ],
            selected_standard_id="std_selected",
            reason="最终选择 std_selected",
        ),
        filter=FilterDecision.model_validate(_filter_payload()["filter"]),
    )

    normalized = _normalize_standard_selection(payload, standards)

    assert normalized.standard_selection is not None
    assert [item.standard_id for item in normalized.standard_selection.evaluations] == [
        "std_selected",
        "std_other",
        "std_fallback",
    ]
    assert [
        item.standard_id
        for item in normalized.standard_selection.evaluations
        if item.matched
    ] == ["std_selected"]


@pytest.mark.asyncio
async def test_filter_schema_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProcessingVisionService(
        Settings(
            ai_tagging_enabled=True,
            ai_tagging_api_key="test-key",
            ai_processing_schema_max_retries=0,
        )
    )
    response = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
    repair_contexts: list[dict[str, str] | None] = []
    schema_retry_calls = 0

    def fake_request(*args):
        repair_contexts.append(args[-1])
        return response

    async def before_schema_retry() -> None:
        nonlocal schema_retry_calls
        schema_retry_calls += 1

    monkeypatch.setattr(service, "_request", fake_request)

    outcome = await service.analyze(
        b"image",
        filter_instruction="保留有效图片",
        before_schema_retry=before_schema_retry,
    )

    assert outcome.status == "failed"
    assert outcome.payload is None
    assert schema_retry_calls == 0
    assert repair_contexts[0] is None
    assert len(repair_contexts) == 1
    assert outcome.diagnostic_json["attempts"] == 1
    assert outcome.diagnostic_json["recovered"] is False


@pytest.mark.asyncio
async def test_filter_schema_failure_stores_redacted_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProcessingVisionService(
        Settings(
            ai_tagging_enabled=True,
            ai_tagging_api_key="test-key",
            ai_processing_schema_max_retries=0,
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
    assert "自动纠错" not in outcome.error_message
    assert outcome.diagnostic_json["attempts"] == 1
    assert outcome.diagnostic_json["recovered"] is False
    diagnostics = json.dumps(outcome.diagnostic_json, ensure_ascii=False)
    assert "secret-token" not in diagnostics
    assert "QUJDRA==" not in diagnostics
    assert "[redacted-image-data]" in diagnostics


def test_strict_schema_unsupported_does_not_issue_fallback_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProcessingVisionService(
        Settings(ai_tagging_enabled=True, ai_tagging_api_key="test-key")
    )
    request_bodies: list[dict[str, object]] = []

    def fake_urlopen(request, *, timeout):
        del timeout
        body = json.loads(request.data.decode())
        request_bodies.append(body)
        raise HTTPError(request.full_url, 400, "unsupported", None, None)

    monkeypatch.setattr(processing_vision_module, "_resize_for_tagging", lambda *_args: b"jpeg")
    monkeypatch.setattr(processing_vision_module, "urlopen", fake_urlopen)
    standard = ProcessingStandard(
        id="standard_test",
        version=1,
        description="测试标准",
        activation_rule="后端已经选定",
        filter_rule="保留有效图片",
    )

    with pytest.raises(HTTPError):
        service._request(
            b"image",
            [standard],
            "reject",
            {},
            None,
            indexed_selection=True,
        )

    assert len(request_bodies) == 1
    body = request_bodies[0]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == (
        "indexed_routed_filter_response"
    )
    prompt = body["messages"][1]["content"][0]["text"]
    assert '"candidate_index": 0' in prompt
    assert "standard_test" not in prompt


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
