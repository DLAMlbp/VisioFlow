import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.schemas.jobs import CreateImageJobRequest
from src.services.images.processing_vision import ProcessingVisionService
from src.services.managed_profiles import ManagedProfileService, standards_from_snapshots
from src.services.profiles import ProcessingStandard, ProfileNotFoundError


def _standard(standard_id: str, priority: int) -> ProcessingStandard:
    return ProcessingStandard(
        id=standard_id,
        name=f"标准 {standard_id}",
        version=1,
        description="条件处理标准",
        activation_rule="图片主体是商品",
        filter_rule="过滤严重模糊或主体遮挡的照片",
        priority=priority,
    )


def _payload(selected_standard_id: str | None) -> dict[str, object]:
    return {
        "standard_selection": {
            "evaluations": [
                {
                    "standard_id": "std_high",
                    "matched": True,
                    "reason": "符合高优先级标准",
                    "confidence": 0.96,
                },
                {
                    "standard_id": "std_low",
                    "matched": False,
                    "reason": "不符合另一类标准",
                    "confidence": 0.93,
                },
            ],
            "selected_standard_id": selected_standard_id,
            "reason": "选择命中的最高优先级标准",
        },
        "filter": {
            "decision": "pass",
            "reason": "画面清晰",
            "confidence": 0.95,
            "dimensions": [
                {"dimension": "清晰度", "passed": True, "reason": "主体边缘清晰"}
            ],
        },
    }


def _route() -> dict[str, str]:
    return {
        "completion_profile": "completion_renovation_v1",
        "completed_filter_profile": "std_high",
        "non_completed_filter_profile": "std_low",
    }


def test_standard_snapshots_are_sorted_by_priority_and_keep_names() -> None:
    snapshots = [
        {"name": "低优先级", "config": _standard("std_low", 10).model_dump()},
        {"name": "高优先级", "config": _standard("std_high", 200).model_dump()},
    ]

    standards = standards_from_snapshots(snapshots)

    assert [standard.id for standard in standards] == ["std_high", "std_low"]
    assert standards[0].name == "高优先级"


@pytest.mark.asyncio
async def test_ai_selection_accepts_one_matching_standard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProcessingVisionService(
        Settings(ai_tagging_enabled=True, ai_tagging_api_key="test-key")
    )
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [
                {"message": {"content": json.dumps(_payload("std_high"), ensure_ascii=False)}}
            ]
        },
    )

    outcome = await service.analyze(
        b"image",
        standards=[_standard("std_high", 200), _standard("std_low", 10)],
    )

    assert outcome.status == "completed"
    assert outcome.payload is not None
    assert outcome.payload.standard_selection is not None
    assert outcome.payload.standard_selection.selected_standard_id == "std_high"


@pytest.mark.asyncio
async def test_ai_selection_normalizes_multiple_matches_when_selection_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProcessingVisionService(
        Settings(ai_tagging_enabled=True, ai_tagging_api_key="test-key")
    )
    invalid_payload = _payload("std_low")
    invalid_payload["standard_selection"]["evaluations"][1]["matched"] = True
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [
                {"message": {"content": json.dumps(invalid_payload, ensure_ascii=False)}}
            ]
        },
    )

    outcome = await service.analyze(
        b"image",
        standards=[_standard("std_high", 200), _standard("std_low", 10)],
    )

    assert outcome.status == "completed"
    assert outcome.payload is not None
    assert outcome.payload.standard_selection is not None
    assert [
        evaluation.standard_id
        for evaluation in outcome.payload.standard_selection.evaluations
        if evaluation.matched
    ] == ["std_low"]


def test_new_job_accepts_paired_filter_standards() -> None:
    payload = CreateImageJobRequest(
        processing_standards=["std_high", "std_low"],
        beautify_profile="bty_natural",
        images=[{"object_key": "uploads/test/image.jpg"}],
    )

    assert payload.processing_standards == ["std_high", "std_low"]


def test_job_request_does_not_require_manual_standard_selection() -> None:
    payload = CreateImageJobRequest(
        beautify_profile="bty_natural",
        images=[{"object_key": "uploads/test/image.jpg"}],
    )

    assert payload.processing_standards == []


def test_standard_preview_keeps_classification_and_filter_rules() -> None:
    compiled = ManagedProfileService(SimpleNamespace(), Settings()).compile_standard(
        classification_rule="图片主体是商品",
        filter_rule="过滤严重模糊或主体遮挡的图片",
        priority=300,
    )

    assert compiled.config["classification_rule"] == "图片主体是商品"
    assert compiled.config["filter_rule"] == "过滤严重模糊或主体遮挡的图片"
    assert compiled.config["priority"] == 300
    assert compiled.config["is_fallback"] is False


def test_new_job_accepts_one_legacy_standard_field_and_server_default_beautify() -> None:
    payload = CreateImageJobRequest(
        processing_standards=["std_product"],
        beautify_profile="bty_natural",
        images=[{"object_key": "uploads/test/image.jpg"}],
    )

    assert payload.processing_standards == ["std_product"]

    defaulted = CreateImageJobRequest(
        filter_route=_route(),
        images=[{"object_key": "uploads/test/image.jpg"}],
    )

    assert defaulted.beautify_profile is None


def test_formal_job_cannot_disable_required_processing_stages() -> None:
    with pytest.raises(ValidationError, match="正式模式固定执行"):
        CreateImageJobRequest(
            processing_standards=["std_finished", "std_unfinished"],
            beautify_profile="bty_natural",
            filter_enabled=False,
            images=[{"object_key": "uploads/test/image.jpg"}],
        )


@pytest.mark.asyncio
async def test_ai_selection_rejects_zero_matching_standards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProcessingVisionService(
        Settings(ai_tagging_enabled=True, ai_tagging_api_key="test-key")
    )
    payload = _payload(None)
    for evaluation in payload["standard_selection"]["evaluations"]:
        evaluation["matched"] = False
    payload["filter"] = {
        "decision": "pass",
        "reason": "没有规则命中，按任务策略保留",
        "confidence": 1.0,
        "dimensions": [
            {"dimension": "规则命中", "passed": True, "reason": "错误地按默认策略放行"}
        ],
    }
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
        },
    )

    outcome = await service.analyze(
        b"image",
        standards=[_standard("std_high", 200), _standard("std_low", 10)],
        unmatched_standard_policy="reject",
    )

    assert outcome.status == "failed"
    assert outcome.payload is None


@pytest.mark.asyncio
async def test_combined_selection_routes_zero_specific_matches_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regular = _standard("std_regular", 100)
    fallback = _standard("std_fallback", 10).model_copy(update={"is_fallback": True})
    payload = _payload(None)
    payload["standard_selection"]["evaluations"] = [
        {
            "standard_id": regular.id,
            "matched": False,
            "reason": "没有明确命中",
            "confidence": 0.82,
        },
        {
            "standard_id": fallback.id,
            "matched": False,
            "reason": "使用兜底标准",
            "confidence": 0.8,
        },
    ]
    payload["standard_selection"]["selected_standard_id"] = fallback.id
    service = ProcessingVisionService(
        Settings(ai_tagging_enabled=True, ai_tagging_api_key="test-key")
    )
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
        },
    )

    outcome = await service.analyze(b"image", standards=[regular, fallback])

    assert outcome.status == "completed"
    assert outcome.payload is not None
    selection = outcome.payload.standard_selection
    assert selection is not None
    assert selection.selected_standard_id == fallback.id
    assert [item.standard_id for item in selection.evaluations if item.matched] == [
        fallback.id
    ]


@pytest.mark.asyncio
async def test_active_standard_resolution_requires_exactly_one_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regular = _standard("std_regular", 100)
    fallback = _standard("std_fallback", 10).model_copy(update={"is_fallback": True})
    rows = [
        SimpleNamespace(
            id=profile.id,
            name=profile.name,
            version=profile.version,
            instruction=profile.classification_rule,
            config_json=profile.model_dump(mode="json"),
        )
        for profile in (regular, fallback)
    ]

    async def list_rows(_self, _profile_type, *, include_inactive=False):
        assert include_inactive is False
        return rows

    monkeypatch.setattr(ManagedProfileService, "list", list_rows)
    resolved = await ManagedProfileService(
        SimpleNamespace(), Settings()
    ).resolve_standards(require_fallback=True)

    assert [profile.id for profile, _ in resolved] == ["std_regular", "std_fallback"]

    rows[1].config_json["is_fallback"] = False
    with pytest.raises(ProfileNotFoundError, match="必须且只能设置一条兜底分类"):
        await ManagedProfileService(SimpleNamespace(), Settings()).resolve_standards(
            require_fallback=True
        )
