import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.schemas.jobs import CreateImageJobRequest
from src.schemas.upload_batches import CreateUploadBatchRequest
from src.services.images.processing_vision import ProcessingVisionService, _user_prompt
from src.services.profiles import ProcessingStandard
from src.workers.completion import _route_snapshot_for_label


def _route() -> dict[str, object]:
    return {
        "completion_profile": "completion_renovation_v1",
        "completed_filter_profile": "standard_completed_v1",
        "non_completed_filter_profile": "standard_non_completed_v1",
        "policy": {
            "insufficient_evidence_policy": "route_non_completed",
            "low_confidence_policy": "continue_with_review",
        },
    }


def test_job_accepts_complete_route_without_legacy_standard_list() -> None:
    payload = CreateImageJobRequest(
        filter_route=_route(),
        beautify_profile="renovation_natural_v1",
        images=[{"object_key": "uploads/test/image.jpg"}],
    )

    assert payload.processing_standards == []
    assert payload.filter_route is not None


def test_route_rejects_same_standard_for_both_branches() -> None:
    route = _route()
    route["non_completed_filter_profile"] = "standard_completed_v1"

    with pytest.raises(ValidationError, match="不能相同"):
        CreateImageJobRequest(
            filter_route=route,
            beautify_profile="renovation_natural_v1",
            images=[{"object_key": "uploads/test/image.jpg"}],
        )


def test_upload_batch_accepts_the_same_route_contract() -> None:
    payload = CreateUploadBatchRequest(
        filter_route=_route(),
        beautify_profile="renovation_natural_v1",
        files=[{"filename": "a.jpg", "content_type": "image/jpeg", "file_size": 100}],
    )

    assert payload.filter_route is not None
    assert payload.filter_route.policy.insufficient_evidence_policy == "route_non_completed"


def test_route_policy_defaults_to_non_completed_for_insufficient_evidence() -> None:
    route = _route()
    route.pop("policy")

    payload = CreateImageJobRequest(
        filter_route=route,
        beautify_profile="renovation_natural_v1",
        images=[{"object_key": "uploads/test/image.jpg"}],
    )

    assert payload.filter_route is not None
    assert payload.filter_route.policy.insufficient_evidence_policy == "route_non_completed"


def test_non_completed_label_uses_non_completed_route() -> None:
    completed = {"id": "standard_completed_v1"}
    non_completed = {"id": "standard_non_completed_v1"}
    job = SimpleNamespace(
        completed_filter_profile_snapshot=completed,
        non_completed_filter_profile_snapshot=non_completed,
    )

    assert _route_snapshot_for_label(job, "non_completed") is non_completed
    assert _route_snapshot_for_label(job, "completed") is completed


def test_non_completed_prompt_never_rejects_only_because_image_is_unfinished() -> None:
    standard = ProcessingStandard(
        id="standard_non_completed_v1",
        name="非完工图片过滤",
        version=1,
        description="非完工分支",
        activation_rule="后端已判定为非完工",
        filter_rule="保留清晰且与装修施工相关的图片",
    )

    prompt = _user_prompt([standard], route_label="non_completed")

    assert "绝不能单独作为 reject 理由" in prompt
    assert "不得执行这些冲突描述" in prompt


@pytest.mark.asyncio
async def test_routed_processing_executes_one_backend_selected_standard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    standard = ProcessingStandard(
        id="standard_completed_v1",
        name="完工过滤",
        version=1,
        description="完工分支",
        activation_rule="后端已路由",
        filter_rule="保留清晰完工照片",
    )
    response = {
        "standard_selection": {
            "selected_candidate_index": 0,
            "reason": "执行唯一分支",
            "confidence": 1,
        },
        "filter": {
            "decision": "pass",
            "reason": "所有维度通过",
            "confidence": 0.95,
            "dimensions": [
                {"dimension": "清晰度", "passed": True, "reason": "主体清晰"}
            ],
        },
        "cover_assessment": {
            "scene_completeness": 5,
            "composition": 5,
            "visual_appeal": 5,
            "representativeness": 5,
            "hard_fail": False,
            "risk_codes": [],
        },
    }
    service = ProcessingVisionService(
        Settings(ai_tagging_enabled=True, ai_tagging_api_key="test")
    )
    monkeypatch.setattr(
        service,
        "_request",
        lambda *_args: {
            "choices": [{"message": {"content": json.dumps(response, ensure_ascii=False)}}]
        },
    )

    outcome = await service.analyze(b"image", standards=[standard])

    assert outcome.status == "completed"
    assert outcome.payload is not None
    assert outcome.payload.standard_selection.selected_standard_id == standard.id
