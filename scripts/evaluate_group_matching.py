from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TextIO

import numpy as np

from src.services.images.group_prototypes import select_diverse_group_prototypes
from src.services.images.similarity import (
    CORE_MODE_EXACT,
    SCORE_VERSION,
    ScoredCandidate,
    decide_similarity,
    score_group_candidates,
)
from src.services.profiles import SimilarityProfile


def load_assets(handle: TextIO) -> list[SimpleNamespace]:
    assets: list[SimpleNamespace] = []
    groups: dict[str, SimpleNamespace] = {}
    for line_number, raw_line in enumerate(handle, start=1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_number} 行不是有效 JSON") from exc
        group_id = str(row.get("group_id") or "").strip()
        asset_id = str(row.get("id") or row.get("asset_id") or "").strip()
        tags = row.get("tags")
        embedding = row.get("embedding")
        if isinstance(embedding, str):
            embedding = json.loads(embedding)
        if not asset_id or not group_id or not isinstance(tags, list):
            raise ValueError(f"第 {line_number} 行缺少 id、group_id 或 tags")
        if not isinstance(embedding, list) or not embedding:
            continue
        group = groups.setdefault(
            group_id,
            SimpleNamespace(id=group_id, tags=tags, status="active"),
        )
        assets.append(
            SimpleNamespace(
                id=asset_id,
                group_id=group_id,
                group=group,
                tags=tags,
                sha256=row.get("sha256"),
                embedding=[float(value) for value in embedding],
                analysis_json=row.get("analysis_json") or {},
                original_filename=row.get("original_filename"),
                thumbnail_object_key=None,
                original_object_key=f"evaluation/{asset_id}",
            )
        )
    return assets


def load_queries(handle: TextIO) -> list[SimpleNamespace]:
    queries: list[SimpleNamespace] = []
    for line_number, raw_line in enumerate(handle, start=1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_number} 行不是有效 JSON") from exc
        query_id = str(row.get("query_id") or row.get("id") or "").strip()
        expected_group_id = row.get("expected_group_id")
        embedding = row.get("embedding")
        if isinstance(embedding, str):
            embedding = json.loads(embedding)
        if not query_id or not isinstance(embedding, list) or not embedding:
            raise ValueError(f"第 {line_number} 行缺少 query_id 或 embedding")
        queries.append(
            SimpleNamespace(
                id=query_id,
                expected_group_id=(
                    str(expected_group_id).strip() if expected_group_id else None
                ),
                sha256=row.get("sha256"),
                embedding=[float(value) for value in embedding],
                analysis_json=row.get("analysis_json") or {},
            )
        )
    return queries


def evaluate(
    assets: list[SimpleNamespace],
    *,
    profile: SimilarityProfile,
    target_precision: float,
    queries: list[SimpleNamespace] | None = None,
    query_ids: set[str] | None = None,
    min_auto_matches: int = 1,
    min_auto_coverage: float = 0.0,
) -> dict[str, Any]:
    by_group: dict[str, list[SimpleNamespace]] = defaultdict(list)
    by_id = {asset.id: asset for asset in assets}
    for asset in assets:
        by_group[asset.group_id].append(asset)

    leave_one_out = queries is None
    evaluation_queries = queries or [
        SimpleNamespace(
            id=asset.id,
            expected_group_id=asset.group_id,
            sha256=asset.sha256,
            embedding=asset.embedding,
            analysis_json=asset.analysis_json,
        )
        for asset in assets
    ]
    outcomes: list[dict[str, Any]] = []
    for query in evaluation_queries:
        if query_ids is not None and query.id not in query_ids:
            continue
        exact_assets = (
            [asset for asset in assets if query.sha256 and asset.sha256 == query.sha256]
            if not leave_one_out
            else []
        )
        prototypes: list[SimpleNamespace] = []
        if not exact_assets:
            for group_assets in by_group.values():
                eligible = [
                    asset
                    for asset in group_assets
                    if not leave_one_out
                    or (
                        asset.id != query.id
                        and (not query.sha256 or asset.sha256 != query.sha256)
                    )
                ]
                prototype_ids = select_diverse_group_prototypes(
                    eligible,
                    limit=profile.similarity_group_max_prototypes,
                )
                prototypes.extend(by_id[asset_id] for asset_id in prototype_ids)
        if leave_one_out and not any(
            asset.group_id == query.expected_group_id for asset in prototypes
        ):
            continue

        candidates: list[ScoredCandidate] = []
        if exact_assets:
            candidates.extend(
                ScoredCandidate(
                    asset=asset,
                    tags=list(asset.group.tags),
                    similarity_score=1.0,
                    feature_score=1.0,
                    final_score=1.0,
                    feature_reliability=1.0,
                    feature_coverage=1.0,
                    core_evidence_mode=CORE_MODE_EXACT,
                    exact_match=True,
                    score_version=SCORE_VERSION,
                )
                for asset in exact_assets
            )
        else:
            query_vector = _normalized(query.embedding)
            matches: list[tuple[SimpleNamespace, float]] = []
            for prototype in prototypes:
                similarity = float(query_vector @ _normalized(prototype.embedding))
                matches.append(
                    (prototype, max(0.0, min(1.0, similarity)))
                )
            candidates = score_group_candidates(
                matches=matches,
                query_content=query.analysis_json,
                settings=profile,
            )
        decision = decide_similarity(candidates=candidates, settings=profile)
        matched_group_id = None
        if decision.matched_asset_id:
            matched_group_id = by_id[decision.matched_asset_id].group_id
        matched_audit = next(
            (
                candidate
                for candidate in decision.candidates
                if candidate.get("asset_id") == decision.matched_asset_id
            ),
            None,
        )
        outcomes.append(
            {
                "query_asset_id": query.id,
                "expected_group_id": query.expected_group_id,
                "matched_group_id": matched_group_id,
                "decision": decision.decision,
                "correct": decision.decision == "matched"
                and matched_group_id == query.expected_group_id,
                "final_score": decision.final_score,
                "group_margin": decision.candidate_margin,
                "matched_candidate_audit": matched_audit,
            }
        )

    automatic = [outcome for outcome in outcomes if outcome["decision"] == "matched"]
    correct = sum(bool(outcome["correct"]) for outcome in automatic)
    precision = correct / len(automatic) if automatic else None
    coverage = len(automatic) / len(outcomes) if outcomes else 0.0
    per_group: dict[str, dict[str, Any]] = {}
    expected_groups = sorted(
        {str(outcome["expected_group_id"]) for outcome in outcomes}
    )
    for group_id in expected_groups:
        group_outcomes = [
            outcome
            for outcome in outcomes
            if str(outcome["expected_group_id"]) == group_id
        ]
        group_automatic = [
            outcome for outcome in group_outcomes if outcome["decision"] == "matched"
        ]
        group_correct = sum(bool(outcome["correct"]) for outcome in group_automatic)
        per_group[group_id] = {
            "samples": len(group_outcomes),
            "automatic_matches": len(group_automatic),
            "automatic_precision": (
                round(group_correct / len(group_automatic), 6)
                if group_automatic
                else None
            ),
            "automatic_coverage": round(
                len(group_automatic) / len(group_outcomes), 6
            ),
        }
    precision_met = precision is not None and precision >= target_precision
    coverage_met = coverage >= min_auto_coverage
    automatic_count_met = len(automatic) >= min_auto_matches
    report = {
        "evaluation_type": (
            "leave_one_asset_out_group_classification"
            if leave_one_out
            else "independent_business_truth_set_group_classification"
        ),
        "asset_count": len(assets),
        "evaluated_count": len(outcomes),
        "automatic_matches": len(automatic),
        "correct_automatic_matches": correct,
        "automatic_precision": round(precision, 6) if precision is not None else None,
        "automatic_coverage": round(coverage, 6),
        "pending_review": sum(
            outcome["decision"] == "pending_review" for outcome in outcomes
        ),
        "unmatched": sum(outcome["decision"] == "unmatched" for outcome in outcomes),
        "target_precision": target_precision,
        "minimum_auto_matches": min_auto_matches,
        "minimum_auto_coverage": min_auto_coverage,
        "precision_gate_passed": precision_met,
        "automatic_count_gate_passed": automatic_count_met,
        "coverage_gate_passed": coverage_met,
        "status": (
            "passed"
            if precision_met and automatic_count_met and coverage_met
            else "failed"
        ),
        "per_expected_group": per_group,
        "limitations": [
            "该结果验证素材组内部一致性，不能代替真实业务图片人工真值集",
            "只有一个独立素材的组无法进行留一验证，已从 evaluated_count 排除",
        ],
        "automatic_errors": [
            outcome
            for outcome in outcomes
            if outcome["decision"] == "matched" and not outcome["correct"]
        ],
        "outcomes": outcomes,
    }
    return report


def threshold_sweep(
    assets: list[SimpleNamespace],
    *,
    profile: SimilarityProfile,
    target_precision: float,
    queries: list[SimpleNamespace] | None,
    min_auto_matches: int,
    min_auto_coverage: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for threshold in (0.60, 0.65, 0.68, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
        sweep_profile = profile.model_copy(
            update={"similarity_auto_threshold": threshold}
        )
        report = evaluate(
            assets,
            profile=sweep_profile,
            target_precision=target_precision,
            queries=queries,
            min_auto_matches=min_auto_matches,
            min_auto_coverage=min_auto_coverage,
        )
        rows.append(
            {
                "auto_threshold": threshold,
                "automatic_matches": report["automatic_matches"],
                "automatic_precision": report["automatic_precision"],
                "automatic_coverage": report["automatic_coverage"],
                "status": report["status"],
            }
        )
    return rows


def _normalized(values: list[float]) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def main() -> None:
    parser = argparse.ArgumentParser(
        description="对素材库执行排除自身后的完整标签组匹配验证"
    )
    parser.add_argument("input", nargs="?", type=Path, help="JSONL；省略时读取 stdin")
    parser.add_argument("--queries", type=Path, help="独立业务真值 JSONL")
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("profiles/tags/library_similarity_v2.yaml"),
    )
    parser.add_argument("--target-precision", type=float, default=0.95)
    parser.add_argument("--min-auto-matches", type=int, default=30)
    parser.add_argument("--min-auto-coverage", type=float, default=0.20)
    parser.add_argument("--threshold-sweep", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--query-id", action="append", default=[])
    args = parser.parse_args()
    if not 0 < args.target_precision <= 1:
        parser.error("--target-precision 必须大于 0 且不超过 1")
    if args.min_auto_matches < 1:
        parser.error("--min-auto-matches 必须至少为 1")
    if not 0 <= args.min_auto_coverage <= 1:
        parser.error("--min-auto-coverage 必须是 0 到 1 之间的数字")

    import yaml

    profile = SimilarityProfile.model_validate(
        yaml.safe_load(args.profile.read_text(encoding="utf-8"))
    )
    if args.input:
        with args.input.open("r", encoding="utf-8") as handle:
            assets = load_assets(handle)
    else:
        assets = load_assets(sys.stdin)
    queries = None
    if args.queries:
        with args.queries.open("r", encoding="utf-8") as handle:
            queries = load_queries(handle)
    report = evaluate(
        assets,
        profile=profile,
        target_precision=args.target_precision,
        queries=queries,
        query_ids=set(args.query_id) if args.query_id else None,
        min_auto_matches=args.min_auto_matches,
        min_auto_coverage=args.min_auto_coverage,
    )
    if args.threshold_sweep:
        report["threshold_sweep"] = threshold_sweep(
            assets,
            profile=profile,
            target_precision=args.target_precision,
            queries=queries,
            min_auto_matches=args.min_auto_matches,
            min_auto_coverage=args.min_auto_coverage,
        )
    if args.summary_only:
        report.pop("outcomes", None)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
