from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from src.services.images.group_match_index import GroupPrototypeSnapshot
from src.services.images.similarity import (
    ScoredCandidate,
    decide_similarity,
    score_group_candidates,
)


@dataclass(frozen=True)
class BenchmarkResult:
    iterations: int
    groups: int
    prototypes_per_group: int
    legacy_p95_ms: float
    index_p95_ms: float
    group_decision_p95_ms: float
    group_p95_ms: float
    p95_delta_ms: float
    allowed_delta_ms: float
    status: str


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile))
    return ordered[index]


def _candidates(groups: int, prototypes_per_group: int) -> list[ScoredCandidate]:
    candidates: list[ScoredCandidate] = []
    for group_index in range(groups):
        group_id = f"grp_{group_index:04d}"
        tags = ["日常", f"业务组{group_index:04d}"]
        for prototype_index in range(prototypes_per_group):
            score = 0.90 - group_index * 0.002 - prototype_index * 0.001
            asset = SimpleNamespace(
                id=f"ast_{group_index:04d}_{prototype_index:02d}",
                group_id=group_id,
                group=SimpleNamespace(id=group_id, tags=tags, status="active"),
                sha256=None,
                original_filename="benchmark.jpg",
                thumbnail_object_key=None,
                original_object_key="library/benchmark.jpg",
            )
            candidates.append(
                ScoredCandidate(
                    asset=asset,
                    tags=tags,
                    similarity_score=score,
                    feature_score=0.85,
                    final_score=score * 0.7 + 0.85 * 0.3,
                    feature_reliability=0.8,
                    feature_coverage=0.8,
                )
            )
    return candidates


def benchmark(
    *,
    iterations: int,
    groups: int,
    prototypes_per_group: int,
    allowed_delta_ms: float,
) -> BenchmarkResult:
    group_candidates = _candidates(groups, prototypes_per_group)
    legacy_candidates = group_candidates[:20]
    random = np.random.default_rng(20260907)
    prototype_assets = [
        SimpleNamespace(
            id=candidate.asset.id,
            group_id=candidate.asset.group_id,
            embedding=random.normal(size=512).tolist(),
            analysis_json={
                "scene": "住宅室内装修施工现场",
                "space": "客厅",
                "condition": "施工中",
                "subjects": ["室内空间"],
                "objects": ["石膏板", "施工工具"],
                "confidence": 0.90,
            },
            sha256=None,
            original_filename="benchmark.jpg",
            thumbnail_object_key=None,
            original_object_key="library/benchmark.jpg",
            group=candidate.asset.group,
        )
        for candidate in group_candidates
    ]
    snapshot = GroupPrototypeSnapshot.build(prototype_assets, generation="benchmark")
    query_embedding = random.normal(size=512).tolist()
    query_content = {
        "scene": "住宅室内装修施工现场",
        "space": "客厅",
        "condition": "施工中",
        "subjects": ["室内空间"],
        "objects": ["石膏板", "施工工具"],
        "confidence": 0.90,
    }
    base_policy = {
        "similarity_auto_threshold": 0.60,
        "similarity_review_threshold": 0.45,
        "similarity_min_margin": 0.05,
        "similarity_image_weight": 0.70,
        "similarity_feature_weight": 0.30,
        "similarity_dynamic_weighting_enabled": True,
        "similarity_min_content_weight": 0.0,
        "similarity_max_content_weight": 0.30,
        "similarity_field_weights": {
            "scene": 0.30,
            "space": 0.20,
            "condition": 0.20,
            "subjects": 0.10,
            "objects": 0.20,
        },
        "similarity_group_visual_best_weight": 0.65,
        "similarity_group_support_threshold": 0.78,
        "similarity_group_content_refine_limit": 16,
        "similarity_group_min_content_confidence": 0.60,
        "similarity_group_fallback_auto_threshold": 0.92,
        "similarity_group_fallback_min_margin": 0.12,
        "similarity_group_fallback_min_support": 2,
        "similarity_group_fallback_min_feature_score": 0.70,
        "similarity_group_fallback_min_feature_strength": 0.10,
        "similarity_semantic_concepts": {},
        "similarity_group_tag_rules": {},
    }
    legacy_policy = SimpleNamespace(
        **base_policy,
        similarity_group_matching_enabled=False,
    )
    group_policy = SimpleNamespace(
        **base_policy,
        similarity_group_matching_enabled=True,
    )

    legacy_times: list[float] = []
    index_times: list[float] = []
    group_decision_times: list[float] = []
    group_times: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        decide_similarity(candidates=legacy_candidates, settings=legacy_policy)
        legacy_times.append((time.perf_counter() - started) * 1000)

        combined_started = time.perf_counter()
        started = time.perf_counter()
        matches = snapshot.score(query_embedding)
        index_times.append((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        scored_groups = score_group_candidates(
            matches=matches,
            query_content=query_content,
            settings=group_policy,
        )
        decide_similarity(candidates=scored_groups, settings=group_policy)
        group_decision_times.append((time.perf_counter() - started) * 1000)
        group_times.append((time.perf_counter() - combined_started) * 1000)

    legacy_p95 = _percentile(legacy_times, 0.95)
    index_p95 = _percentile(index_times, 0.95)
    group_decision_p95 = _percentile(group_decision_times, 0.95)
    group_p95 = _percentile(group_times, 0.95)
    delta = group_p95 - legacy_p95
    return BenchmarkResult(
        iterations=iterations,
        groups=groups,
        prototypes_per_group=prototypes_per_group,
        legacy_p95_ms=round(legacy_p95, 6),
        index_p95_ms=round(index_p95, 6),
        group_decision_p95_ms=round(group_decision_p95, 6),
        group_p95_ms=round(group_p95, 6),
        p95_delta_ms=round(delta, 6),
        allowed_delta_ms=allowed_delta_ms,
        status="passed" if delta <= allowed_delta_ms else "failed",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark legacy candidate ranking against group-level matching"
    )
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument(
        "--groups",
        type=int,
        default=167,
        help="默认 167 组 × 6 张代表图，覆盖约千张素材的极端规模",
    )
    parser.add_argument("--prototypes-per-group", type=int, default=6)
    parser.add_argument("--allowed-delta-ms", type=float, default=10.0)
    args = parser.parse_args()
    if args.iterations < 10 or args.groups < 1 or args.prototypes_per_group < 1:
        parser.error("iterations 至少为10，groups和prototypes-per-group必须大于0")
    result = benchmark(
        iterations=args.iterations,
        groups=args.groups,
        prototypes_per_group=args.prototypes_per_group,
        allowed_delta_ms=args.allowed_delta_ms,
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    if result.status != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
