from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.services.images.similarity import reliability_weighted_similarity_score


AUTO_THRESHOLD = 0.70
REVIEW_THRESHOLD = 0.70
MIN_GROUP_MARGIN = 0.03
SEMANTIC_BONUS = 0.06
UNSUPPORTED_AUTO_THRESHOLD = 0.70
FALLBACK_AUTO_ENABLED = False
FALLBACK_AUTO_THRESHOLD = 0.92
FALLBACK_MIN_MARGIN = 0.12
FALLBACK_MIN_SUPPORT = 2
FALLBACK_MIN_FEATURE_SCORE = 0.70
FALLBACK_MIN_FEATURE_STRENGTH = 0.10


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    expected_asset_id: str | None
    top1_asset_id: str | None
    top1_score: float
    top1_ranking_score: float
    top1_similarity_score: float
    top1_feature_score: float | None
    top1_feature_reliability: float | None
    top1_feature_coverage: float | None
    top2_score: float
    margin: float
    core_requirements_passed: bool
    core_evidence_mode: str
    group_support_count: int
    correct: bool
    top5: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class CandidateResult:
    candidate_id: str
    similarity_score: float
    feature_score: float | None
    feature_reliability: float | None
    feature_coverage: float | None
    final_score: float
    ranking_score: float
    expected_id: str | None
    core_requirements_passed: bool
    core_evidence_mode: str
    group_support_count: int


def load_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise ValueError("JSON 输入必须是对象数组")
        return payload
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def summarize_queries(
    rows: list[dict[str, Any]],
    *,
    min_content_weight: float = 0.0,
    max_content_weight: float = 0.30,
    review_threshold: float = REVIEW_THRESHOLD,
    semantic_bonus: float = SEMANTIC_BONUS,
    fallback_auto_enabled: bool = FALLBACK_AUTO_ENABLED,
) -> list[QueryResult]:
    grouped: dict[str, list[CandidateResult]] = defaultdict(list)
    for row in rows:
        query_id = str(row.get("query_id", "")).strip()
        candidate_id = str(row.get("candidate_asset_id", "")).strip()
        expected_raw = str(row.get("expected_asset_id", "")).strip()
        expected_raw = str(row.get("expected_group_id") or expected_raw).strip()
        candidate_id = str(row.get("candidate_group_id") or candidate_id).strip()
        if not query_id or not candidate_id:
            raise ValueError("每行必须包含 query_id 和 candidate_asset_id")
        try:
            similarity_score = float(row["similarity_score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("similarity_score 必须是 0 到 1 之间的数字") from exc
        if not 0 <= similarity_score <= 1:
            raise ValueError("similarity_score 必须是 0 到 1 之间的数字")
        feature_raw = row.get("feature_score")
        feature_score = (
            float(feature_raw)
            if feature_raw is not None and str(feature_raw).strip()
            else None
        )
        if feature_score is not None and not 0 <= feature_score <= 1:
            raise ValueError("feature_score 必须是 0 到 1 之间的数字")
        reliability_raw = row.get("feature_reliability")
        feature_reliability = (
            float(reliability_raw)
            if reliability_raw is not None and str(reliability_raw).strip()
            else None
        )
        if feature_reliability is not None and not 0 <= feature_reliability <= 1:
            raise ValueError("feature_reliability 必须是 0 到 1 之间的数字")
        coverage_raw = row.get("feature_coverage")
        feature_coverage = (
            float(coverage_raw)
            if coverage_raw is not None and str(coverage_raw).strip()
            else None
        )
        if feature_coverage is not None and not 0 <= feature_coverage <= 1:
            raise ValueError("feature_coverage 必须是 0 到 1 之间的数字")
        final_raw = row.get("final_score")
        if final_raw is not None and str(final_raw).strip():
            final_score = float(final_raw)
        elif feature_score is not None:
            if feature_reliability is None:
                raise ValueError(
                    "使用 reliability_v4 校准时，feature_score 必须同时提供 "
                    "feature_reliability 或 final_score"
                )
            if feature_coverage is None:
                raise ValueError(
                    "使用 reliability_v4 校准时，feature_score 必须同时提供 "
                    "feature_coverage 或 final_score"
                )
            final_score = reliability_weighted_similarity_score(
                similarity_score=similarity_score,
                feature_score=feature_score,
                feature_reliability=feature_reliability,
                feature_coverage=feature_coverage,
                min_content_weight=min_content_weight,
                max_content_weight=max_content_weight,
            ).final_score
        else:
            final_score = similarity_score
        if not 0 <= final_score <= 1:
            raise ValueError("final_score 必须是 0 到 1 之间的数字")
        core_requirements_passed = (
            str(row.get("core_requirements_passed", "true")).strip().lower()
            not in {"0", "false", "no"}
        )
        core_evidence_mode = str(
            row.get("core_evidence_mode")
            or ("supported" if core_requirements_passed else "conflicted")
        ).strip()
        if core_evidence_mode == "rejected":
            core_evidence_mode = "unsupported"
        core_score_raw = row.get("core_evidence_score")
        core_evidence_score = (
            float(core_score_raw)
            if core_score_raw is not None and str(core_score_raw).strip()
            else (1.0 if core_evidence_mode == "supported" else 0.0)
        )
        if not 0 <= core_evidence_score <= 1:
            raise ValueError("core_evidence_score 必须是 0 到 1 之间的数字")
        ranking_raw = row.get("ranking_score")
        ranking_score = (
            float(ranking_raw)
            if ranking_raw is not None and str(ranking_raw).strip()
            else _ranking_score(
                final_score,
                core_evidence_mode=core_evidence_mode,
                core_evidence_score=core_evidence_score,
                semantic_bonus=semantic_bonus,
            )
        )
        try:
            group_support_count = int(row.get("group_support_count") or 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("group_support_count 必须是正整数") from exc
        if group_support_count < 1:
            raise ValueError("group_support_count 必须是正整数")
        grouped[query_id].append(
            CandidateResult(
                candidate_id=candidate_id,
                similarity_score=similarity_score,
                feature_score=feature_score,
                feature_reliability=feature_reliability,
                feature_coverage=feature_coverage,
                final_score=final_score,
                ranking_score=ranking_score,
                expected_id=expected_raw or None,
                core_requirements_passed=core_requirements_passed,
                core_evidence_mode=core_evidence_mode,
                group_support_count=group_support_count,
            )
        )

    results: list[QueryResult] = []
    for query_id, candidates in grouped.items():
        expected_values = {candidate.expected_id for candidate in candidates}
        if len(expected_values) != 1:
            raise ValueError(f"query_id={query_id} 的 expected_asset_id 不一致")
        expected = expected_values.pop()
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                candidate.ranking_score,
                candidate.similarity_score,
                candidate.candidate_id,
            ),
            reverse=True,
        )
        eligible = [
            candidate
            for candidate in ranked
            if candidate.core_evidence_mode != "conflicted"
            and (
                candidate.core_evidence_mode != "fallback"
                or fallback_auto_enabled
            )
        ]
        viable = [
            candidate
            for candidate in eligible
            if candidate.final_score >= review_threshold
        ]
        selection_pool = viable or eligible or ranked
        top1 = selection_pool[0]
        comparable = (
            [candidate for candidate in eligible if candidate.core_evidence_mode == "exact"]
            if top1.core_evidence_mode == "exact"
            else eligible
        )
        top2_score = comparable[1].ranking_score if len(comparable) > 1 else 0.0
        results.append(
            QueryResult(
                query_id=query_id,
                expected_asset_id=expected,
                top1_asset_id=top1.candidate_id,
                top1_score=top1.final_score,
                top1_ranking_score=top1.ranking_score,
                top1_similarity_score=top1.similarity_score,
                top1_feature_score=top1.feature_score,
                top1_feature_reliability=top1.feature_reliability,
                top1_feature_coverage=top1.feature_coverage,
                top2_score=top2_score,
                margin=(
                    top1.ranking_score - top2_score if len(comparable) > 1 else 1.0
                ),
                core_requirements_passed=top1.core_requirements_passed,
                core_evidence_mode=top1.core_evidence_mode,
                group_support_count=top1.group_support_count,
                correct=expected is not None and top1.candidate_id == expected,
                top5=tuple(
                    {
                        "asset_id": candidate.candidate_id,
                        "similarity_score": candidate.similarity_score,
                        "feature_score": candidate.feature_score,
                        "feature_reliability": candidate.feature_reliability,
                        "feature_coverage": candidate.feature_coverage,
                        "final_score": candidate.final_score,
                        "expected": candidate.candidate_id == expected,
                        "core_requirements_passed": candidate.core_requirements_passed,
                        "core_evidence_mode": candidate.core_evidence_mode,
                        "group_support_count": candidate.group_support_count,
                    }
                    for candidate in ranked[:5]
                ),
            )
        )
    return sorted(results, key=lambda result: result.query_id)


def _ranking_score(
    final_score: float,
    *,
    core_evidence_mode: str,
    core_evidence_score: float,
    semantic_bonus: float,
) -> float:
    if core_evidence_mode == "exact":
        return 2.0
    if core_evidence_mode == "supported":
        return final_score + semantic_bonus * core_evidence_score
    return final_score


def calibrate(
    queries: list[QueryResult],
    *,
    target_precision: float = 0.97,
    target_review_recall: float = 0.95,
    min_auto_matches: int = 30,
    min_positive_samples: int = 0,
    min_negative_samples: int = 0,
    min_group_margin: float = MIN_GROUP_MARGIN,
    auto_threshold: float = AUTO_THRESHOLD,
    review_threshold: float = REVIEW_THRESHOLD,
    fallback_auto_threshold: float = FALLBACK_AUTO_THRESHOLD,
    unsupported_auto_threshold: float = UNSUPPORTED_AUTO_THRESHOLD,
    fallback_auto_enabled: bool = FALLBACK_AUTO_ENABLED,
    fallback_min_margin: float = FALLBACK_MIN_MARGIN,
    fallback_min_support: int = FALLBACK_MIN_SUPPORT,
    fallback_min_feature_score: float = FALLBACK_MIN_FEATURE_SCORE,
    fallback_min_feature_strength: float = FALLBACK_MIN_FEATURE_STRENGTH,
) -> dict[str, Any]:
    if not queries:
        raise ValueError("没有可校准的人工标注样本")
    automatic = [
        query
        for query in queries
        if _automatic_match_allowed(
            query,
            auto_threshold=auto_threshold,
            min_group_margin=min_group_margin,
            fallback_auto_threshold=fallback_auto_threshold,
            unsupported_auto_threshold=unsupported_auto_threshold,
            fallback_auto_enabled=fallback_auto_enabled,
            fallback_min_margin=fallback_min_margin,
            fallback_min_support=fallback_min_support,
            fallback_min_feature_score=fallback_min_feature_score,
            fallback_min_feature_strength=fallback_min_feature_strength,
        )
    ]
    automatic_correct = sum(query.correct for query in automatic)
    automatic_precision = (
        automatic_correct / len(automatic) if automatic else None
    )
    correct_top1 = sum(query.correct for query in queries)
    positive_queries = [query for query in queries if query.expected_asset_id is not None]
    negative_queries = [query for query in queries if query.expected_asset_id is None]
    retrievable_positives = sum(
        query.top1_score >= review_threshold
        and any(candidate["expected"] for candidate in query.top5)
        for query in positive_queries
    )
    review_recall = (
        retrievable_positives / len(positive_queries) if positive_queries else None
    )
    enough_samples = (
        len(positive_queries) >= min_positive_samples
        and len(negative_queries) >= min_negative_samples
    )
    enough_automatic = len(automatic) >= min_auto_matches
    precision_met = (
        automatic_precision is not None and automatic_precision >= target_precision
    )
    review_recall_met = review_recall is not None and review_recall >= target_review_recall
    ready = enough_samples and enough_automatic and precision_met and review_recall_met
    evaluation = {
        "auto_threshold": auto_threshold,
        "review_threshold": review_threshold,
        "minimum_group_margin": min_group_margin,
        "fallback_auto_threshold": fallback_auto_threshold,
        "unsupported_auto_threshold": unsupported_auto_threshold,
        "fallback_auto_enabled": fallback_auto_enabled,
        "fallback_minimum_group_margin": fallback_min_margin,
        "fallback_minimum_support": fallback_min_support,
        "decision_rule": (
            "runtime-equivalent semantic priority, score, group margin, core evidence, "
            "and fallback support gates"
        ),
        "auto_matches": len(automatic),
        "correct_auto_matches": automatic_correct,
        "precision": (
            round(automatic_precision, 6) if automatic_precision is not None else None
        ),
        "coverage": round(len(automatic) / len(queries), 6),
        "review_recall": round(review_recall, 6) if review_recall is not None else None,
    }
    return {
        "sample_count": len(queries),
        "target_precision": target_precision,
        "target_review_recall": target_review_recall,
        "minimum_auto_matches": min_auto_matches,
        "positive_sample_count": len(positive_queries),
        "negative_sample_count": len(negative_queries),
        "minimum_positive_samples": min_positive_samples,
        "minimum_negative_samples": min_negative_samples,
        "top1_accuracy": round(correct_top1 / len(queries), 6),
        "evaluation": evaluation,
        "recommendation": evaluation if ready else None,
        "status": "recommended" if ready else "insufficient_evidence",
        "message": (
            "固定阈值策略已满足样本量、自动匹配精确率和复核召回率要求"
            if ready
            else "固定阈值策略的样本量、自动匹配精确率或复核召回率尚未达到上线要求"
        ),
        "queries": [asdict(query) for query in queries],
    }


def _automatic_match_allowed(
    query: QueryResult,
    *,
    auto_threshold: float,
    min_group_margin: float,
    fallback_auto_threshold: float,
    unsupported_auto_threshold: float,
    fallback_auto_enabled: bool,
    fallback_min_margin: float,
    fallback_min_support: int,
    fallback_min_feature_score: float,
    fallback_min_feature_strength: float,
) -> bool:
    if query.core_evidence_mode == "conflicted":
        return False
    if query.core_evidence_mode == "unsupported":
        return (
            query.top1_score >= unsupported_auto_threshold
            and query.margin >= min_group_margin
        )
    if query.core_evidence_mode != "fallback":
        return query.top1_score >= auto_threshold and query.margin >= min_group_margin
    feature_strength = (
        (query.top1_feature_reliability or 0.0)
        * (query.top1_feature_coverage or 0.0)
    )
    return (
        fallback_auto_enabled
        and query.top1_score >= fallback_auto_threshold
        and query.margin >= fallback_min_margin
        and query.group_support_count >= fallback_min_support
        and query.top1_feature_score is not None
        and query.top1_feature_score >= fallback_min_feature_score
        and feature_strength >= fallback_min_feature_strength
    )
def main() -> None:
    parser = argparse.ArgumentParser(
        description="根据人工标注候选对校准图片向量与内容特征混合匹配阈值"
    )
    parser.add_argument("input", type=Path, help="CSV 或 JSON 标注文件")
    parser.add_argument("--output", type=Path, help="JSON 报告输出路径")
    parser.add_argument("--target-precision", type=float, default=0.97)
    parser.add_argument("--target-review-recall", type=float, default=0.95)
    parser.add_argument("--min-auto-matches", type=int, default=30)
    parser.add_argument("--min-positive-samples", type=int, default=300)
    parser.add_argument("--min-negative-samples", type=int, default=300)
    parser.add_argument("--min-group-margin", type=float, default=MIN_GROUP_MARGIN)
    parser.add_argument("--auto-threshold", type=float, default=AUTO_THRESHOLD)
    parser.add_argument("--review-threshold", type=float, default=REVIEW_THRESHOLD)
    parser.add_argument(
        "--fallback-auto-threshold", type=float, default=FALLBACK_AUTO_THRESHOLD
    )
    parser.add_argument(
        "--unsupported-auto-threshold", type=float, default=UNSUPPORTED_AUTO_THRESHOLD
    )
    parser.add_argument("--fallback-auto-enabled", action="store_true")
    parser.add_argument(
        "--fallback-min-margin", type=float, default=FALLBACK_MIN_MARGIN
    )
    parser.add_argument(
        "--fallback-min-support", type=int, default=FALLBACK_MIN_SUPPORT
    )
    args = parser.parse_args()
    if not 0 < args.target_precision <= 1:
        parser.error("--target-precision 必须大于 0 且不超过 1")
    if args.min_auto_matches < 1:
        parser.error("--min-auto-matches 必须至少为 1")
    if not 0 < args.target_review_recall <= 1:
        parser.error("--target-review-recall 必须大于 0 且不超过 1")
    if args.min_positive_samples < 0 or args.min_negative_samples < 0:
        parser.error("最小正负样本数不能小于 0")
    if not 0 <= args.min_group_margin <= 1:
        parser.error("--min-group-margin 必须是 0 到 1 之间的数字")
    for name in (
        "auto_threshold",
        "review_threshold",
        "fallback_auto_threshold",
        "unsupported_auto_threshold",
        "fallback_min_margin",
    ):
        if not 0 <= getattr(args, name) <= 1:
            parser.error(f"--{name.replace('_', '-')} 必须是 0 到 1 之间的数字")
    if args.fallback_min_support < 1:
        parser.error("--fallback-min-support 必须至少为 1")
    report = calibrate(
        summarize_queries(load_rows(args.input), review_threshold=args.review_threshold),
        target_precision=args.target_precision,
        target_review_recall=args.target_review_recall,
        min_auto_matches=args.min_auto_matches,
        min_positive_samples=args.min_positive_samples,
        min_negative_samples=args.min_negative_samples,
        min_group_margin=args.min_group_margin,
        auto_threshold=args.auto_threshold,
        review_threshold=args.review_threshold,
        fallback_auto_threshold=args.fallback_auto_threshold,
        unsupported_auto_threshold=args.unsupported_auto_threshold,
        fallback_auto_enabled=args.fallback_auto_enabled,
        fallback_min_margin=args.fallback_min_margin,
        fallback_min_support=args.fallback_min_support,
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)


if __name__ == "__main__":
    main()
