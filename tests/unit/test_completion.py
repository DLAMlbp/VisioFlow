import pytest

from src.services.images.completion import CompletionModelPayload, normalize_completion


def _payload(**facts: bool) -> CompletionModelPayload:
    values = {
        "is_real_photo": True,
        "is_indoor_space": True,
        "is_assessable": True,
        "no_obvious_construction": True,
        "hard_finish_complete": True,
        "finished_space_evidence": True,
        "usable_or_display_ready": True,
    }
    values.update(facts)
    return CompletionModelPayload(
        facts=values,
        model_label="completed",
        model_subtype="completed",
        confidence=0.95,
        reason_codes=[],
        reason="可见装修空间",
    )


def test_all_completion_facts_normalize_to_completed() -> None:
    result = normalize_completion(_payload(), review_confidence=0.8)

    assert result.label == "completed"
    assert result.subtype == "completed"
    assert result.review_required is False


@pytest.mark.parametrize("field", [
    "no_obvious_construction",
    "hard_finish_complete",
    "finished_space_evidence",
    "usable_or_display_ready",
])
def test_any_missing_completion_fact_cannot_be_completed(field: str) -> None:
    result = normalize_completion(_payload(**{field: False}), review_confidence=0.8)

    assert result.label == "non_completed"
    assert result.subtype == "construction"
    assert "LABEL_NORMALIZED" in result.reason_codes


def test_invalid_and_unassessable_images_are_not_routed_as_construction() -> None:
    invalid = normalize_completion(
        _payload(is_real_photo=False), review_confidence=0.8
    )
    insufficient = normalize_completion(
        _payload(is_assessable=False), review_confidence=0.8
    )

    assert invalid.label == "non_completed"
    assert invalid.subtype == "invalid_or_irrelevant"
    assert insufficient.label == "non_completed"
    assert insufficient.subtype == "insufficient_evidence"
    assert insufficient.review_required is True


def test_low_confidence_requires_review() -> None:
    payload = _payload()
    payload.confidence = 0.79

    assert normalize_completion(payload, review_confidence=0.8).review_required is True
