from types import SimpleNamespace

import pytest

from src.services.images.processing_vision import (
    ActivationEvaluation,
    FilterDecision,
    FilterDimensionResult,
    ProcessingVisionOutcome,
    ProcessingVisionPayload,
    StandardSelection,
)
from src.services.profiles import ProcessingStandard
from src.workers import completion


def _standard(standard_id: str, *, fallback: bool = False) -> ProcessingStandard:
    return ProcessingStandard(
        id=standard_id,
        name=standard_id,
        version=3,
        description=standard_id,
        classification_rule=f"识别 {standard_id}",
        filter_rule=f"过滤 {standard_id}",
        is_fallback=fallback,
    )


class _Repository:
    saved: dict[str, object] | None = None

    async def complete_combined_classification_filter(self, _item, **values) -> bool:
        type(self).saved = values
        return True

    async def fail_combined_classification_filter(self, *_args, **_kwargs) -> bool:
        raise AssertionError("valid combined output must not fail the item")


@pytest.mark.asyncio
@pytest.mark.parametrize(("decision", "expected_passed"), [("pass", True), ("reject", False)])
async def test_combined_call_persists_classification_and_filter_atomically(
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
    expected_passed: bool,
) -> None:
    standards = [_standard("std_finished"), _standard("std_fallback", fallback=True)]
    selected = standards[0]
    payload = ProcessingVisionPayload(
        standard_selection=StandardSelection(
            evaluations=[
                ActivationEvaluation(
                    standard_id=selected.id,
                    matched=True,
                    reason="画面直接可见已完成空间",
                    confidence=0.96,
                ),
                ActivationEvaluation(
                    standard_id="std_fallback",
                    matched=False,
                    reason="无需兜底",
                    confidence=0.9,
                ),
            ],
            selected_standard_id=selected.id,
            reason="明确命中完工标准",
        ),
        filter=FilterDecision(
            decision=decision,
            reason="审核维度完成",
            confidence=0.94,
            dimensions=[
                FilterDimensionResult(
                    dimension="内容有效性",
                    passed=expected_passed,
                    reason="主体清晰" if expected_passed else "主体无法辨认",
                )
            ],
        ),
    )

    class Service:
        def __init__(self, _settings) -> None:
            pass

        async def analyze(self, *_args, **_kwargs):
            return ProcessingVisionOutcome(
                status="completed",
                payload=payload,
                duration_ms=1234,
            )

    advanced: list[str] = []
    monkeypatch.setattr(completion, "ProcessingVisionService", Service)
    monkeypatch.setattr(
        completion,
        "_advance_after_preprocess",
        lambda _repository, item: _record_advance(advanced, item.id),
    )
    repository = _Repository()
    item = SimpleNamespace(
        id="img_test",
        job_id="job_test",
        width=1600,
        height=1200,
        metric=None,
    )
    job = SimpleNamespace(
        processing_standard_snapshots=[standard.model_dump() for standard in standards]
    )
    settings = SimpleNamespace(
        ai_tagging_model="gpt-5.6-sol",
        completion_review_confidence=0.8,
    )

    await completion._classify_and_filter_standard(
        repository,
        item,
        job,
        settings,
        b"image",
        standards,
    )

    assert repository.saved is not None
    assert repository.saved["passed"] is expected_passed
    assert repository.saved["routed_filter_profile_id"] == selected.id
    assert repository.saved["routed_filter_profile_version"] == selected.version
    assert repository.saved["completion_payload"]["normalized"]["selected_standard_id"] == selected.id
    assert repository.saved["processing_payload"]["filter"]["decision"] == decision
    assert advanced == ["img_test"]


async def _record_advance(target: list[str], image_id: str) -> None:
    target.append(image_id)

