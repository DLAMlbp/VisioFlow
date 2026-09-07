"""Validate the approved performance-plan dataset without inventing ground truth.

A pass checks completeness and file identity only. Human accuracy assessment,
paired runtime results and deployment acceptance remain separate gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

QUOTAS = {
    "clear_classification": 40,
    "difficult_filtering": 40,
    "redaction": 20,
    "matching_boundary": 20,
    "image_edge_cases": 20,
}


def validate_dataset(manifest: dict, root: Path) -> dict:
    errors: list[str] = []
    cases = manifest.get("cases", [])
    if not isinstance(cases, list):
        return {"status": "not_ready", "errors": ["cases must be a list"]}
    root = root.resolve()
    counts: Counter[str] = Counter()
    identities: set[str] = set()
    hashes: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"case[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix}: expected object")
            continue
        identifier = case.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(f"{prefix}: missing id")
        elif identifier in identities:
            errors.append(f"{prefix}: duplicate id")
        else:
            identities.add(identifier)
        category = case.get("category")
        if not isinstance(category, str) or category not in QUOTAS:
            errors.append(f"{prefix}: unassigned or invalid category")
        else:
            counts[category] += 1
        if case.get("review_status") != "approved":
            errors.append(f"{prefix}: human review is pending")
        for field in ("reviewed_by", "reviewed_at"):
            value = case.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}: missing {field}")
        decision = case.get("expected_filter_decision")
        if decision not in ("pass", "reject", "invalid_input"):
            errors.append(f"{prefix}: missing expected filter decision")
        if category in ("clear_classification", "difficult_filtering"):
            label = case.get("expected_classification")
            if not isinstance(label, str) or not label.strip():
                errors.append(f"{prefix}: missing expected classification")
        if category == "matching_boundary":
            # [] explicitly means no permissible match/tags; null is unreviewed.
            for field in ("allowed_match_asset_ids", "expected_tags"):
                value = case.get(field)
                if not isinstance(value, list) or any(
                    not isinstance(item, str) or not item.strip() for item in value
                ):
                    errors.append(f"{prefix}: missing or invalid {field}")
        if category == "redaction" and not isinstance(
            case.get("existing_logo_expectation") or case.get("expected_redaction"), dict
        ):
            errors.append(f"{prefix}: missing redaction expectations")

        filename = case.get("file")
        if not isinstance(filename, str) or not filename:
            errors.append(f"{prefix}: missing file")
            continue
        path = (root / filename).resolve()
        if not path.is_relative_to(root):
            errors.append(f"{prefix}: file is outside the dataset root")
            continue
        if not path.is_file():
            errors.append(f"{prefix}: file does not exist")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != case.get("sha256"):
            errors.append(f"{prefix}: file content differs from reviewed hash")
        if digest in hashes:
            errors.append(f"{prefix}: duplicate image content")
        hashes.add(digest)
    for category, required in QUOTAS.items():
        if counts[category] < required:
            errors.append(f"{category}: requires {required}, found {counts[category]}")
    if len(hashes) < sum(QUOTAS.values()):
        errors.append(f"requires 140 unique source files, found {len(hashes)}")
    return {
        "status": "ready_for_quality_evaluation" if not errors else "not_ready",
        "cases": len(cases),
        "unique_files": len(hashes),
        "categories": dict(counts),
        "errors": errors,
        "scope": "dataset completeness only; not a quality or deployment approval",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            raise TypeError("manifest must be an object")
        report = validate_dataset(manifest, args.root)
    except (OSError, ValueError, TypeError) as exc:
        report = {"status": "not_ready", "errors": [str(exc)]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{report['status']}: {len(report.get('errors', []))} issues; {args.output}")
    return 0 if report["status"] == "ready_for_quality_evaluation" else 2


if __name__ == "__main__":
    raise SystemExit(main())
