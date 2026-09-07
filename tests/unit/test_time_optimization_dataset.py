import hashlib

import pytest

from scripts.validate_time_optimization_dataset import QUOTAS, validate_dataset


@pytest.fixture
def reviewed_dataset(tmp_path):
    cases = []
    for category, count in QUOTAS.items():
        for _ in range(count):
            number = len(cases)
            # File-identity fixtures, not classification/quality ground truth.
            content = f"identity-fixture-{number}".encode()
            filename = f"{number}.jpg"
            (tmp_path / filename).write_bytes(content)
            cases.append({
                "id": str(number), "file": filename,
                "sha256": hashlib.sha256(content).hexdigest(),
                "category": category, "review_status": "approved",
                "reviewed_by": "fixture-reviewer", "reviewed_at": "2026-09-07",
                "expected_filter_decision": "reject", "expected_classification": "fixture",
                "allowed_match_asset_ids": [], "expected_tags": [],
                "expected_redaction": {"boxes": [], "preserve_text": []},
            })
    return {"cases": cases}


def test_complete_manifest_only_passes_completeness_gate(tmp_path, reviewed_dataset):
    report = validate_dataset(reviewed_dataset, tmp_path)
    assert report["status"] == "ready_for_quality_evaluation"
    assert report["unique_files"] == 140
    assert "not a quality or deployment approval" in report["scope"]


def test_copied_source_cannot_pad_acceptance_sample_count(tmp_path, reviewed_dataset):
    first, second = reviewed_dataset["cases"][:2]
    second.update(file=first["file"], sha256=first["sha256"])
    report = validate_dataset(reviewed_dataset, tmp_path)
    assert report["status"] == "not_ready"
    assert any("duplicate image content" in issue for issue in report["errors"])


@pytest.mark.parametrize("field,value", [
    ("review_status", "pending"), ("reviewed_by", None), ("expected_classification", None),
    ("expected_filter_decision", None), ("category", "unknown"),
])
def test_unreviewed_cases_never_pass(tmp_path, reviewed_dataset, field, value):
    reviewed_dataset["cases"][0][field] = value
    assert validate_dataset(reviewed_dataset, tmp_path)["status"] == "not_ready"


def test_changed_file_requires_new_review(tmp_path, reviewed_dataset):
    (tmp_path / "0.jpg").write_bytes(b"changed")
    report = validate_dataset(reviewed_dataset, tmp_path)
    assert any("differs from reviewed hash" in issue for issue in report["errors"])


def test_files_outside_root_are_not_read(tmp_path, reviewed_dataset):
    reviewed_dataset["cases"][0]["file"] = "../outside.jpg"
    report = validate_dataset(reviewed_dataset, tmp_path)
    assert any("outside the dataset root" in issue for issue in report["errors"])


def test_unknown_match_labels_are_distinct_from_confirmed_no_match(tmp_path, reviewed_dataset):
    case = next(c for c in reviewed_dataset["cases"] if c["category"] == "matching_boundary")
    case["allowed_match_asset_ids"] = None
    report = validate_dataset(reviewed_dataset, tmp_path)
    assert any("allowed_match_asset_ids" in issue for issue in report["errors"])
