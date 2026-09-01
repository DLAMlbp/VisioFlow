import json

import pytest

from src.core.config import Settings
from src.services.images.classification import (
    StandardClassificationVisionService,
    _classification_prompt,
)
from src.services.profiles import ProcessingStandard


def _standards() -> list[ProcessingStandard]:
    return [
        ProcessingStandard(
            id="std_completed",
            name="完工图片",
            version=1,
            description="完工分类与过滤",
            classification_rule="无明显施工且装修空间可使用",
            filter_rule="只保留清晰、完整展示装修成果的图片 SECRET_FILTER_ONE",
        ),
        ProcessingStandard(
            id="std_construction",
            name="施工图片",
            version=1,
            description="施工分类与过滤",
            classification_rule="未达到完工条件或仍可见施工状态",
            filter_rule="只保留施工区域清晰可辨的图片 SECRET_FILTER_TWO",
        ),
    ]


def _response(*, multiple: bool = False) -> dict[str, object]:
    return {
        "evaluations": [
            {
                "standard_id": "std_completed",
                "matched": True,
                "reason": "空间已经完工",
                "confidence": 0.96,
            },
            {
                "standard_id": "std_construction",
                "matched": multiple,
                "reason": "未见施工状态",
                "confidence": 0.92,
            },
        ],
        "selected_standard_id": "std_completed",
        "reason": "可见完整成品空间",
        "content_analysis": {
            "summary": "完整的室内客厅空间",
            "content_type": "室内照片",
            "scene": "已布置完成的客厅",
            "spaces": ["客厅"],
            "view": "整体视角",
            "subjects": ["客厅空间"],
            "objects": ["沙发", "茶几"],
            "visible_conditions": ["墙面和地面完整", "家具已布置"],
            "attributes": {"材质": ["木质", "织物"]},
            "supporting_evidence": ["未见施工工具", "空间具备使用条件"],
            "conflicting_evidence": [],
            "missing_evidence": [],
            "uncertainties": [],
            "ocr_text": [],
            "confidence": 0.94,
        },
    }


def _fallback_standards() -> list[ProcessingStandard]:
    standards = _standards()
    standards[1] = standards[1].model_copy(update={"is_fallback": True})
    return standards


def test_classification_prompt_never_contains_filter_rules() -> None:
    prompt = _classification_prompt(_standards())

    assert "无明显施工且装修空间可使用" in prompt
    assert "SECRET_FILTER_ONE" not in prompt
    assert "SECRET_FILTER_TWO" not in prompt
    assert "不执行过滤" in prompt


@pytest.mark.asyncio
async def test_classification_selects_exactly_one_paired_standard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = StandardClassificationVisionService(
        Settings(ai_tagging_enabled=True, ai_tagging_api_key="test")
    )
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [{"message": {"content": json.dumps(_response(), ensure_ascii=False)}}]
        },
    )

    outcome = await service.analyze(b"image", standards=_standards())

    assert outcome.status == "completed"
    assert outcome.payload is not None
    assert outcome.payload.selected_standard_id == "std_completed"
    assert outcome.payload.content_analysis.spaces == ["客厅"]
    assert outcome.payload.content_analysis.confidence == 0.94


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ocr_text", "expected"),
    [
        ("施工日志", ["施工日志"]),
        ([f"文字{i}" for i in range(25)], [f"文字{i}" for i in range(20)]),
        (["施工日志", "施工日志", "  ", None], ["施工日志"]),
    ],
)
async def test_classification_normalizes_provider_ocr_variations(
    monkeypatch: pytest.MonkeyPatch,
    ocr_text: object,
    expected: list[str],
) -> None:
    response = _response()
    response["content_analysis"]["ocr_text"] = ocr_text
    service = StandardClassificationVisionService(
        Settings(
            ai_tagging_enabled=True,
            ai_tagging_api_key="test",
            ai_processing_schema_max_retries=0,
        )
    )
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [{"message": {"content": json.dumps(response, ensure_ascii=False)}}]
        },
    )

    outcome = await service.analyze(b"image", standards=_standards())

    assert outcome.status == "completed"
    assert outcome.payload is not None
    assert outcome.payload.content_analysis.ocr_text == expected


@pytest.mark.asyncio
async def test_classification_rejects_multiple_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = StandardClassificationVisionService(
        Settings(
            ai_tagging_enabled=True,
            ai_tagging_api_key="test",
            ai_processing_schema_max_retries=0,
        )
    )
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [
                {"message": {"content": json.dumps(_response(multiple=True), ensure_ascii=False)}}
            ]
        },
    )

    outcome = await service.analyze(b"image", standards=_standards())

    assert outcome.status == "failed"
    assert outcome.error_message == "多个明确分类标准同时命中"


@pytest.mark.asyncio
async def test_classification_retries_when_content_analysis_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response()
    del response["content_analysis"]["visible_conditions"]
    service = StandardClassificationVisionService(
        Settings(
            ai_tagging_enabled=True,
            ai_tagging_api_key="test",
            ai_processing_schema_max_retries=0,
        )
    )
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [{"message": {"content": json.dumps(response, ensure_ascii=False)}}]
        },
    )

    outcome = await service.analyze(b"image", standards=_standards())

    assert outcome.status == "failed"
    assert outcome.error_message == (
        "AI 图片分类响应字段不合法：content_analysis.visible_conditions"
    )


@pytest.mark.asyncio
async def test_zero_specific_matches_routes_to_the_single_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response()
    for evaluation in response["evaluations"]:
        evaluation["matched"] = False
    response["selected_standard_id"] = "std_completed"
    service = StandardClassificationVisionService(
        Settings(ai_tagging_enabled=True, ai_tagging_api_key="test")
    )
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [{"message": {"content": json.dumps(response, ensure_ascii=False)}}]
        },
    )

    outcome = await service.analyze(b"image", standards=_fallback_standards())

    assert outcome.status == "completed"
    assert outcome.selected is not None
    assert outcome.selected.standard_id == "std_construction"
    assert outcome.payload is not None
    assert outcome.payload.selected_standard_id == "std_construction"


def test_classification_prompt_identifies_fallback_without_filter_rules() -> None:
    prompt = _classification_prompt(_fallback_standards())

    assert '"is_fallback": true' in prompt
    assert "只有没有任何明确标准命中" in prompt
    assert "SECRET_FILTER_TWO" not in prompt


def test_classification_prompt_requires_evidence_bounded_reasons() -> None:
    prompt = _classification_prompt(_fallback_standards())

    assert "先写图片中直接可见" in prompt
    assert "无法确定的物体或用途必须使用“疑似”" in prompt
    assert "现有信息不足以判断整体装修是否完成" in prompt
    assert "证据不足不等于确认尚未完工" in prompt
    assert "不得照抄分类规则中的抽象措辞" in prompt
    assert "supporting_evidence" in prompt
    assert "missing_evidence" in prompt
    assert "内容识别完整性和可靠性的总体置信度" in prompt
    assert "ocr_text 必须是 JSON 字符串数组" in prompt
    assert "最多 10 条" in prompt
