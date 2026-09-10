from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Protocol

from src.models.library_asset import LibraryAsset

SCORE_VERSION = "group_reliability_v2"

CORE_MODE_DISABLED = "disabled"
CORE_MODE_EXACT = "exact"
CORE_MODE_SUPPORTED = "supported"
CORE_MODE_FALLBACK = "fallback"
CORE_MODE_UNSUPPORTED = "unsupported"
CORE_MODE_CONFLICTED = "conflicted"
CORE_MODE_REJECTED = CORE_MODE_UNSUPPORTED
_CONTENT_NOT_PROVIDED = object()

DEFAULT_FEATURE_FIELD_WEIGHTS: dict[str, float] = {
    "scene": 0.15,
    "space": 0.12,
    "condition": 0.12,
    "content_type": 0.10,
    "view": 0.05,
    "subjects": 0.12,
    "objects": 0.12,
    "ocr_text": 0.12,
    "attributes": 0.05,
    "features": 0.05,
}

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "space": ("space", "spaces"),
    "condition": ("condition", "visible_conditions"),
}

_CANONICAL_CONCEPTS: dict[str, dict[str, tuple[str, ...]]] = {
    "scene": {
        "construction": ("施工", "装修现场", "工地", "毛坯"),
        "completed": ("完工", "竣工", "装修完成"),
        "ceremony": ("开工仪式", "开工庆祝", "庆祝活动", "活动合影"),
        "document": ("文档", "报价单", "表格", "合同"),
        "exterior": ("建筑外部", "小区外", "门头", "户外"),
        "interior": ("室内", "房间内部", "建筑内部"),
    },
    "space": {
        "kitchen": ("厨房",),
        "living_room": ("客厅",),
        "bedroom": ("卧室",),
        "bathroom": ("卫生间", "浴室"),
        "hallway": ("走廊", "通道"),
        "ceiling": ("顶棚", "吊顶", "顶部"),
        "wall": ("墙面", "墙体", "墙角"),
        "floor": ("地面", "地板"),
        "room": ("室内", "房间", "建筑内部"),
    },
    "condition": {
        "construction": ("施工中", "正在施工", "未完成", "裸露", "毛坯"),
        "completed": ("已完工", "完工", "竣工", "已完成"),
        "ceremony": ("开工", "庆祝", "仪式", "布置"),
        "damaged": ("破损", "损坏", "斑驳"),
        "protected": ("保护膜", "成品保护", "覆盖保护"),
    },
    "content_type": {
        "photo": ("照片", "现场照片", "实景"),
        "document": ("文档", "表格", "报价单", "合同"),
        "screenshot": ("截图", "屏幕"),
        "illustration": ("插画", "效果图", "渲染图"),
    },
    "view": {
        "close": ("特写", "近景", "局部"),
        "medium": ("中景",),
        "wide": ("广角", "远景", "整体", "全景"),
        "front": ("正面",),
        "upward": ("仰拍", "仰视"),
        "overhead": ("俯拍", "俯视"),
    },
}

_MUTUALLY_EXCLUSIVE: dict[str, set[frozenset[str]]] = {
    "condition": {frozenset(("construction", "completed"))},
    "content_type": {
        frozenset(("photo", "document")),
        frozenset(("photo", "screenshot")),
        frozenset(("photo", "illustration")),
    },
}


class SimilarityPolicy(Protocol):
    similarity_image_weight: float
    similarity_feature_weight: float
    similarity_dynamic_weighting_enabled: bool
    similarity_min_content_weight: float
    similarity_max_content_weight: float
    similarity_field_weights: dict[str, float]
    similarity_auto_threshold: float
    similarity_min_margin: float
    similarity_group_matching_enabled: bool
    similarity_group_visual_best_weight: float
    similarity_group_support_threshold: float
    similarity_group_content_refine_limit: int
    similarity_group_min_content_confidence: float
    similarity_group_semantic_bonus: float
    similarity_group_unsupported_auto_threshold: float
    similarity_group_fallback_auto_enabled: bool
    similarity_group_fallback_auto_threshold: float
    similarity_group_fallback_min_margin: float
    similarity_group_fallback_min_support: int
    similarity_group_fallback_min_feature_score: float
    similarity_group_fallback_min_feature_strength: float
    similarity_semantic_concepts: dict[str, list[str]]
    similarity_group_tag_rules: dict[str, object]


@dataclass(frozen=True)
class FeatureSimilarityEvidence:
    score: float | None
    reliability: float
    coverage: float
    field_scores: dict[str, dict[str, object]]


@dataclass(frozen=True)
class SimilarityScoreBreakdown:
    final_score: float
    image_weight: float
    feature_weight: float


@dataclass(frozen=True)
class _ComparisonEvidence:
    score: float
    reliability: float
    method: str


@dataclass(frozen=True)
class ScoredCandidate:
    asset: LibraryAsset
    tags: list[str]
    similarity_score: float
    feature_score: float | None
    final_score: float
    feature_reliability: float = 0.0
    feature_coverage: float = 0.0
    image_weight: float = 1.0
    feature_weight: float = 0.0
    field_scores: dict[str, dict[str, object]] = field(default_factory=dict)
    core_requirements_passed: bool = True
    core_evidence_score: float | None = None
    core_evidence: dict[str, object] = field(default_factory=dict)
    core_evidence_mode: str = CORE_MODE_DISABLED
    ranking_score: float | None = None
    group_prototype_count: int = 1
    group_support_count: int = 1
    exact_match: bool = False
    score_version: str = SCORE_VERSION


@dataclass(frozen=True)
class SimilarityDecision:
    decision: str
    message: str
    matched_asset_id: str | None
    tags: list[str]
    similarity_score: float | None
    feature_score: float | None
    final_score: float | None
    feature_reliability: float | None
    feature_coverage: float | None
    candidate_margin: float | None
    field_scores: dict[str, dict[str, object]]
    score_version: str
    candidates: list[dict[str, object]]


def feature_similarity_evidence(
    query: dict[str, object] | None,
    candidate: dict[str, object] | None,
    *,
    field_weights: dict[str, float] | None = None,
) -> FeatureSimilarityEvidence:
    """Score only comparable content fields and report their effective coverage."""
    if not query or not candidate:
        return FeatureSimilarityEvidence(
            score=None, reliability=0.0, coverage=0.0, field_scores={}
        )

    weights = field_weights or DEFAULT_FEATURE_FIELD_WEIGHTS
    total_weight = sum(max(0.0, float(weight)) for weight in weights.values())
    if total_weight <= 0:
        return FeatureSimilarityEvidence(
            score=None, reliability=0.0, coverage=0.0, field_scores={}
        )

    field_scores: dict[str, dict[str, object]] = {}
    weighted_score = 0.0
    effective_weight = 0.0
    available_weight = 0.0
    for field_name, raw_weight in weights.items():
        weight = max(0.0, float(raw_weight))
        if weight <= 0:
            continue
        left_value = _field_value(query, field_name)
        right_value = _field_value(candidate, field_name)
        if field_name in {"scene", "space", "condition", "content_type", "view"}:
            evidence = _text_evidence(field_name, left_value, right_value)
        elif field_name in {"subjects", "objects", "ocr_text"}:
            evidence = _list_evidence(
                field_name, _string_list(left_value), _string_list(right_value)
            )
        elif field_name in {"attributes", "features"}:
            evidence = _mapping_evidence(left_value, right_value)
        else:
            continue
        if evidence is None:
            continue

        field_reliability = min(
            _field_confidence(query, field_name),
            _field_confidence(candidate, field_name),
        ) * evidence.reliability
        field_effective_weight = weight * field_reliability
        available_weight += weight
        weighted_score += evidence.score * field_effective_weight
        effective_weight += field_effective_weight
        field_scores[field_name] = {
            "score": round(evidence.score, 6),
            "reliability": round(field_reliability, 6),
            "weight": round(weight, 6),
            "effective_weight": round(field_effective_weight, 6),
            "method": evidence.method,
        }

    if effective_weight <= 0:
        return FeatureSimilarityEvidence(
            score=None,
            reliability=0.0,
            coverage=_clamp(available_weight / total_weight),
            field_scores=field_scores,
        )
    return FeatureSimilarityEvidence(
        score=_clamp(weighted_score / effective_weight),
        reliability=_clamp(effective_weight / available_weight),
        coverage=_clamp(available_weight / total_weight),
        field_scores=field_scores,
    )


def feature_similarity(
    query: dict[str, object] | None, candidate: dict[str, object] | None
) -> float | None:
    """Backward-compatible score-only wrapper for callers outside matching workers."""
    return feature_similarity_evidence(query, candidate).score


def reliability_weighted_similarity_score(
    *,
    similarity_score: float,
    feature_score: float | None,
    feature_reliability: float,
    feature_coverage: float = 1.0,
    min_content_weight: float = 0.0,
    max_content_weight: float = 0.30,
) -> SimilarityScoreBreakdown:
    similarity_score = _clamp(similarity_score)
    if feature_score is None:
        return SimilarityScoreBreakdown(similarity_score, 1.0, 0.0)
    minimum = _clamp(min_content_weight)
    maximum = max(minimum, _clamp(max_content_weight))
    evidence_strength = _clamp(feature_reliability) * _clamp(feature_coverage)
    content_weight = minimum + (maximum - minimum) * evidence_strength
    image_weight = 1.0 - content_weight
    final_score = similarity_score * image_weight + _clamp(feature_score) * content_weight
    return SimilarityScoreBreakdown(_clamp(final_score), image_weight, content_weight)


def combined_similarity_score(
    *,
    similarity_score: float,
    feature_score: float | None,
    settings: SimilarityPolicy,
    feature_reliability: float | None = None,
    feature_coverage: float = 1.0,
) -> float:
    if feature_score is None:
        return _clamp(similarity_score)
    if settings.similarity_dynamic_weighting_enabled and feature_reliability is not None:
        return reliability_weighted_similarity_score(
            similarity_score=similarity_score,
            feature_score=feature_score,
            feature_reliability=feature_reliability,
            feature_coverage=feature_coverage,
            min_content_weight=settings.similarity_min_content_weight,
            max_content_weight=settings.similarity_max_content_weight,
        ).final_score
    total_weight = settings.similarity_image_weight + settings.similarity_feature_weight
    if total_weight <= 0:
        return _clamp(similarity_score)
    return _clamp(
        (
            similarity_score * settings.similarity_image_weight
            + feature_score * settings.similarity_feature_weight
        )
        / total_weight
    )


def score_candidate(
    *,
    asset: LibraryAsset,
    tags: list[str],
    similarity_score: float,
    query_content: dict[str, object] | None,
    settings: SimilarityPolicy,
    candidate_content: dict[str, object] | None | object = _CONTENT_NOT_PROVIDED,
) -> ScoredCandidate:
    content = (
        asset.analysis_json
        if candidate_content is _CONTENT_NOT_PROVIDED
        else candidate_content
    )
    evidence = feature_similarity_evidence(
        query_content,
        content if isinstance(content, dict) else None,
        field_weights=settings.similarity_field_weights,
    )
    if settings.similarity_dynamic_weighting_enabled:
        breakdown = reliability_weighted_similarity_score(
            similarity_score=similarity_score,
            feature_score=evidence.score,
            feature_reliability=evidence.reliability,
            feature_coverage=evidence.coverage,
            min_content_weight=settings.similarity_min_content_weight,
            max_content_weight=settings.similarity_max_content_weight,
        )
        score_version = SCORE_VERSION
    else:
        final_score = combined_similarity_score(
            similarity_score=similarity_score,
            feature_score=evidence.score,
            settings=settings,
        )
        total_weight = settings.similarity_image_weight + settings.similarity_feature_weight
        feature_weight = (
            settings.similarity_feature_weight / total_weight
            if evidence.score is not None and total_weight > 0
            else 0.0
        )
        breakdown = SimilarityScoreBreakdown(
            final_score=final_score,
            image_weight=1.0 - feature_weight,
            feature_weight=feature_weight,
        )
        score_version = "legacy_v2"
    core_passed, core_score, core_mode, core_evidence = _group_core_evidence(
        tags=tags,
        query=query_content,
        settings=settings,
    )
    return ScoredCandidate(
        asset=asset,
        tags=tags,
        similarity_score=_clamp(similarity_score),
        feature_score=evidence.score,
        final_score=breakdown.final_score,
        feature_reliability=evidence.reliability,
        feature_coverage=evidence.coverage,
        image_weight=breakdown.image_weight,
        feature_weight=breakdown.feature_weight,
        field_scores=evidence.field_scores,
        core_requirements_passed=core_passed,
        core_evidence_score=core_score,
        core_evidence=core_evidence,
        core_evidence_mode=core_mode,
        ranking_score=_semantic_ranking_score(
            breakdown.final_score,
            core_mode=core_mode,
            core_evidence_score=core_score,
            settings=settings,
        ),
        score_version=score_version,
    )


def score_group_candidates(
    *,
    matches: list[tuple[LibraryAsset, float]],
    query_content: dict[str, object] | None,
    settings: SimilarityPolicy,
) -> list[ScoredCandidate]:
    """Collapse every group before textual scoring while keeping full visual coverage."""
    grouped: dict[str, list[tuple[LibraryAsset, float]]] = {}
    for asset, similarity_score in matches:
        grouped.setdefault(asset.group_id, []).append((asset, _clamp(similarity_score)))

    scored: list[ScoredCandidate] = []
    ranked_assets: dict[str, list[tuple[LibraryAsset, float]]] = {}
    best_weight = _clamp(
        getattr(settings, "similarity_group_visual_best_weight", 0.65)
    )
    support_threshold = getattr(settings, "similarity_group_support_threshold", 0.78)
    for group_id in sorted(grouped):
        ranked = sorted(
            grouped[group_id],
            key=lambda item: (item[1], item[0].id),
            reverse=True,
        )
        top = ranked[:3]
        visual_scores = [score for _asset, score in top]
        visual_mean = sum(visual_scores) / len(visual_scores)
        group_visual_score = best_weight * visual_scores[0] + (
            1.0 - best_weight
        ) * visual_mean
        representative = top[0][0]
        tags = list(representative.group.tags)
        core_passed, core_score, core_mode, core_evidence = _group_core_evidence(
            tags=tags,
            query=query_content,
            settings=settings,
        )
        support_count = sum(score >= support_threshold for _asset, score in ranked)
        group_fields = {
            "_group": {
            "prototype_count": len(ranked),
            "content_consensus_count": 0,
            "support_count": support_count,
            "support_threshold": round(support_threshold, 6),
            "best_visual_score": round(visual_scores[0], 6),
            "top_visual_mean": round(visual_mean, 6),
            "aggregated_visual_score": round(group_visual_score, 6),
            }
        }
        scored.append(
            ScoredCandidate(
                asset=representative,
                tags=tags,
                similarity_score=group_visual_score,
                feature_score=None,
                final_score=group_visual_score,
                field_scores=group_fields,
                core_requirements_passed=core_passed,
                core_evidence_score=core_score,
                core_evidence=core_evidence,
                core_evidence_mode=core_mode,
                ranking_score=_semantic_ranking_score(
                    group_visual_score,
                    core_mode=core_mode,
                    core_evidence_score=core_score,
                    settings=settings,
                ),
                group_prototype_count=len(ranked),
                group_support_count=support_count,
            )
        )
        ranked_assets[group_id] = ranked

    refine_limit = getattr(settings, "similarity_group_content_refine_limit", 16)
    refine_ids: set[str] = set()
    for mode in (CORE_MODE_SUPPORTED, CORE_MODE_FALLBACK):
        candidates = sorted(
            (candidate for candidate in scored if candidate.core_evidence_mode == mode),
            key=lambda item: (item.similarity_score, item.asset.id),
            reverse=True,
        )
        refine_ids.update(candidate.asset.group_id for candidate in candidates[:refine_limit])

    refined: list[ScoredCandidate] = []
    for candidate in scored:
        if candidate.asset.group_id not in refine_ids or query_content is None:
            refined.append(candidate)
            continue
        top = ranked_assets[candidate.asset.group_id][:3]
        group_content = _merge_group_analysis(
            [asset.analysis_json for asset, _score in top]
        )
        enriched = score_candidate(
            asset=candidate.asset,
            tags=candidate.tags,
            similarity_score=candidate.similarity_score,
            query_content=query_content,
            settings=settings,
            candidate_content=group_content,
        )
        group_fields = dict(enriched.field_scores)
        group_metadata = dict(candidate.field_scores["_group"])
        group_metadata["content_consensus_count"] = len(top)
        group_fields["_group"] = group_metadata
        refined.append(
            replace(
                enriched,
                field_scores=group_fields,
                group_prototype_count=candidate.group_prototype_count,
                group_support_count=candidate.group_support_count,
            )
        )
    return refined


def _merge_group_analysis(
    payloads: list[dict[str, object] | None],
) -> dict[str, object] | None:
    valid = [payload for payload in payloads if payload]
    if not valid:
        return None
    merged: dict[str, object] = {}
    for field_name in ("scene", "space", "condition", "content_type", "view"):
        values = _unique_strings(
            value
            for payload in valid
            for value in _flatten_strings(_field_value(payload, field_name))
        )
        if values:
            merged[field_name] = "；".join(values)
    for field_name in ("subjects", "objects", "ocr_text"):
        values = _unique_strings(
            value
            for payload in valid
            for value in _flatten_strings(_field_value(payload, field_name))
        )
        if values:
            merged[field_name] = values
    for field_name in ("attributes", "features"):
        combined: dict[str, list[str]] = {}
        for payload in valid:
            value = _field_value(payload, field_name)
            if not isinstance(value, dict):
                continue
            for key, nested in value.items():
                if not isinstance(key, str) or not key.strip():
                    continue
                combined.setdefault(key, []).extend(_flatten_strings(nested))
        if combined:
            merged[field_name] = {
                key: _unique_strings(values) for key, values in combined.items()
            }
    merged["content_confidence"] = sum(
        _field_confidence(payload, "__group__") for payload in valid
    ) / len(valid)
    return merged


def _unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip().casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(value.strip())
    return result


def decide_similarity(
    *,
    candidates: list[ScoredCandidate],
    settings: SimilarityPolicy,
) -> SimilarityDecision:
    if not candidates:
        return unmatched_decision("无法识别")

    deduplicated, collapsed_same_image = _collapse_same_image_candidates(candidates)
    ranked = _rank_group_candidates(deduplicated, settings=settings)
    # The displayed final score is the sole adoption criterion. Semantic
    # evidence still contributes to scoring, but is not a separate veto.
    ranked.sort(key=lambda item: (-item.final_score, -item.similarity_score, item.asset.id))
    best = ranked[0]
    margin = (
        best.final_score - ranked[1].final_score
        if len(ranked) > 1
        else 1.0
    )
    auto_threshold = settings.similarity_auto_threshold
    if best.final_score >= auto_threshold:
        decision = "matched"
        message = (
            "已匹配到同组中的相同素材"
            if collapsed_same_image
            else "综合匹配分已达标，已采用最高分素材组的完整标签"
        )
    else:
        decision = "unmatched"
        message = "最高综合匹配分未达到自动采用线，未继承素材组标签"

    serialized = [
        {
            "asset_id": candidate.asset.id,
            "group_id": candidate.asset.group_id,
            "original_filename": candidate.asset.original_filename,
            "preview_object_key": (
                candidate.asset.thumbnail_object_key or candidate.asset.original_object_key
            ),
            "tags": candidate.tags,
            "score_version": candidate.score_version,
            "similarity_score": round(candidate.similarity_score, 4),
            "feature_score": (
                round(candidate.feature_score, 4)
                if candidate.feature_score is not None
                else None
            ),
            "feature_reliability": round(candidate.feature_reliability, 4),
            "feature_coverage": round(candidate.feature_coverage, 4),
            "image_weight": round(candidate.image_weight, 4),
            "feature_weight": round(candidate.feature_weight, 4),
            "raw_score": round(candidate.final_score, 4),
            "final_score": round(candidate.final_score, 4),
            "field_scores": candidate.field_scores,
            "core_requirements_passed": candidate.core_requirements_passed,
            "core_evidence_score": candidate.core_evidence_score,
            "core_evidence": candidate.core_evidence,
            "core_evidence_mode": candidate.core_evidence_mode,
            "ranking_score": round(candidate.final_score, 4),
            "group_prototype_count": candidate.group_prototype_count,
            "group_support_count": candidate.group_support_count,
        }
        for candidate in ranked[:10]
    ]
    return SimilarityDecision(
        decision=decision,
        message=message,
        matched_asset_id=None if decision == "unmatched" else best.asset.id,
        tags=best.tags if decision == "matched" else [],
        similarity_score=best.similarity_score,
        feature_score=best.feature_score,
        final_score=best.final_score,
        feature_reliability=best.feature_reliability,
        feature_coverage=best.feature_coverage,
        candidate_margin=margin,
        field_scores=best.field_scores,
        score_version=best.score_version,
        candidates=serialized,
    )


def unmatched_decision(message: str) -> SimilarityDecision:
    return SimilarityDecision(
        decision="unmatched",
        message=message,
        matched_asset_id=None,
        tags=[],
        similarity_score=None,
        feature_score=None,
        final_score=None,
        feature_reliability=None,
        feature_coverage=None,
        candidate_margin=None,
        field_scores={},
        score_version=SCORE_VERSION,
        candidates=[],
    )


def _collapse_same_image_candidates(
    candidates: list[ScoredCandidate],
) -> tuple[list[ScoredCandidate], bool]:
    """Treat byte-identical reference images as one candidate with the broadest tags."""
    grouped: dict[str, list[ScoredCandidate]] = {}
    for candidate in candidates:
        sha256 = candidate.asset.sha256
        key = (
            f"group:{candidate.asset.group_id}:sha256:{sha256}"
            if sha256
            else f"asset:{candidate.asset.id}"
        )
        grouped.setdefault(key, []).append(candidate)

    collapsed = [
        max(
            group,
            key=lambda item: (
                item.final_score,
                item.similarity_score,
                item.asset.id,
            ),
        )
        for group in grouped.values()
    ]
    return (
        sorted(collapsed, key=lambda item: item.final_score, reverse=True),
        any(len(group) > 1 for group in grouped.values()),
    )


def _rank_group_candidates(
    candidates: list[ScoredCandidate], *, settings: SimilarityPolicy
) -> list[ScoredCandidate]:
    """Aggregate independent tag groups while retaining a representative asset."""
    grouped: dict[str, list[ScoredCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.asset.group_id, []).append(candidate)
    representatives: list[ScoredCandidate] = []
    group_matching = getattr(settings, "similarity_group_matching_enabled", False)
    best_weight = _clamp(
        getattr(settings, "similarity_group_visual_best_weight", 0.65)
    )
    for group in grouped.values():
        representative = max(
            group,
            key=lambda item: (
                item.final_score,
                item.similarity_score,
                item.feature_reliability,
                item.asset.id,
            ),
        )
        if not group_matching or len(group) == 1:
            representatives.append(representative)
            continue
        visual_scores = sorted(
            (candidate.similarity_score for candidate in group), reverse=True
        )[:3]
        visual_mean = sum(visual_scores) / len(visual_scores)
        group_visual_score = best_weight * visual_scores[0] + (1.0 - best_weight) * visual_mean
        support_threshold = getattr(settings, "similarity_group_support_threshold", 0.78)
        support_count = sum(
            candidate.similarity_score >= support_threshold for candidate in group
        )
        content_candidates = [
            candidate for candidate in group if candidate.feature_score is not None
        ]
        if content_candidates:
            content_candidates.sort(
                key=lambda item: (
                    item.feature_score or 0.0,
                    item.feature_reliability * item.feature_coverage,
                ),
                reverse=True,
            )
            top_content = content_candidates[:3]
            feature_scores = [float(item.feature_score or 0.0) for item in top_content]
            group_feature_score = 0.6 * feature_scores[0] + 0.4 * (
                sum(feature_scores) / len(feature_scores)
            )
            group_feature_reliability = sum(
                item.feature_reliability for item in top_content
            ) / len(top_content)
            group_feature_coverage = sum(
                item.feature_coverage for item in top_content
            ) / len(top_content)
        else:
            group_feature_score = None
            group_feature_reliability = 0.0
            group_feature_coverage = 0.0
        breakdown = reliability_weighted_similarity_score(
            similarity_score=group_visual_score,
            feature_score=group_feature_score,
            feature_reliability=group_feature_reliability,
            feature_coverage=group_feature_coverage,
            min_content_weight=getattr(settings, "similarity_min_content_weight", 0.0),
            max_content_weight=getattr(settings, "similarity_max_content_weight", 0.30),
        )
        group_fields = dict(representative.field_scores)
        group_fields["_group"] = {
            "prototype_count": len(group),
            "support_count": support_count,
            "support_threshold": round(support_threshold, 6),
            "best_visual_score": round(visual_scores[0], 6),
            "top_visual_mean": round(visual_mean, 6),
            "aggregated_visual_score": round(group_visual_score, 6),
        }
        representatives.append(
            replace(
                representative,
                similarity_score=group_visual_score,
                feature_score=group_feature_score,
                final_score=breakdown.final_score,
                feature_reliability=group_feature_reliability,
                feature_coverage=group_feature_coverage,
                image_weight=breakdown.image_weight,
                feature_weight=breakdown.feature_weight,
                ranking_score=_semantic_ranking_score(
                    breakdown.final_score,
                    core_mode=representative.core_evidence_mode,
                    core_evidence_score=representative.core_evidence_score,
                    settings=settings,
                ),
                field_scores=group_fields,
                group_prototype_count=len(group),
                group_support_count=support_count,
            )
        )
    return sorted(
        representatives,
        key=lambda item: (
            _candidate_ranking_score(item, settings),
            item.similarity_score,
            item.asset.id,
        ),
        reverse=True,
    )


def _group_core_evidence(
    *,
    tags: list[str],
    query: dict[str, object] | None,
    settings: SimilarityPolicy,
) -> tuple[bool, float | None, str, dict[str, object]]:
    if not getattr(settings, "similarity_group_matching_enabled", False):
        return True, None, CORE_MODE_DISABLED, {}
    rules = getattr(settings, "similarity_group_tag_rules", {}) or {}
    expected: dict[str, list[tuple[str, object]]] = {}
    for tag in tags:
        rule = rules.get(tag)
        if rule is not None:
            expected.setdefault(str(_rule_value(rule, "dimension")), []).append((tag, rule))
    if not expected:
        return True, None, CORE_MODE_FALLBACK, {
            "dimensions": {},
            "reason": "group_has_no_core_rules",
        }
    if not query:
        return False, 0.0, CORE_MODE_REJECTED, {
            "dimensions": {},
            "reason": "content_analysis_missing",
        }

    confidence = _field_confidence(query, "__group__")
    minimum_confidence = getattr(
        settings, "similarity_group_min_content_confidence", 0.60
    )
    dimension_results: dict[str, object] = {}
    passed_dimensions = 0
    dimension_scores: list[float] = []
    hard_gate_failed = False
    for dimension, tag_rules in expected.items():
        matched_tags: list[str] = []
        matched_keywords: list[str] = []
        matched_concepts: list[str] = []
        missing_concepts: list[str] = []
        forbidden_concepts: list[str] = []
        best_rule_score = 0.0
        dimension_hard_gate = False
        for tag, rule in tag_rules:
            configured_hard_gate = _rule_value(rule, "hard_gate")
            dimension_hard_gate = dimension_hard_gate or (
                bool(configured_hard_gate)
                if configured_hard_gate is not None
                else dimension in {"space", "content", "subject", "event"}
            )
            fields = list(_rule_value(rule, "fields") or [])
            haystack = _rule_haystack(query, fields)
            keywords = [str(value).strip() for value in _rule_value(rule, "keywords") or []]
            hits = [keyword for keyword in keywords if keyword and keyword.casefold() in haystack]
            minimum_hits = int(_rule_value(rule, "minimum_keyword_hits", 1))
            required = [
                str(value).strip()
                for value in _rule_value(rule, "required_concepts", []) or []
                if str(value).strip()
            ]
            supporting = [
                str(value).strip()
                for value in _rule_value(rule, "supporting_concepts", []) or []
                if str(value).strip()
            ]
            forbidden = [
                str(value).strip()
                for value in _rule_value(rule, "forbidden_concepts", []) or []
                if str(value).strip()
            ]
            concepts = getattr(settings, "similarity_semantic_concepts", {}) or {}
            required_hits = [
                concept
                for concept in required
                if _semantic_concept_present(haystack, concept, concepts)
            ]
            supporting_hits = [
                concept
                for concept in supporting
                if _semantic_concept_present(haystack, concept, concepts)
            ]
            forbidden_hits = [
                concept
                for concept in forbidden
                if _semantic_concept_present(haystack, concept, concepts)
            ]
            direct_match = bool(keywords) and len(set(hits)) >= minimum_hits
            required_match = bool(required) and len(required_hits) == len(required)
            rule_passed = (direct_match or required_match) and not forbidden_hits
            if direct_match:
                rule_score = 1.0
            elif required:
                required_ratio = len(required_hits) / len(required)
                supporting_ratio = (
                    len(supporting_hits) / len(supporting) if supporting else 0.0
                )
                rule_score = required_ratio * (0.85 if supporting else 1.0)
                if supporting:
                    rule_score += supporting_ratio * 0.15
            else:
                rule_score = 0.0
            if forbidden_hits:
                rule_score = 0.0
            best_rule_score = max(best_rule_score, rule_score)
            if rule_passed:
                matched_tags.append(tag)
                matched_keywords.extend(hits)
                matched_concepts.extend(required_hits + supporting_hits)
            missing_concepts.extend(
                concept for concept in required if concept not in required_hits
            )
            forbidden_concepts.extend(forbidden_hits)
        dimension_passed = bool(matched_tags) and confidence >= minimum_confidence
        if dimension_passed:
            passed_dimensions += 1
        elif dimension_hard_gate:
            hard_gate_failed = True
        dimension_scores.append(best_rule_score)
        dimension_results[dimension] = {
            "passed": dimension_passed,
            "score": round(best_rule_score, 6),
            "expected_tags": [tag for tag, _rule in tag_rules],
            "matched_tags": matched_tags,
            "matched_keywords": sorted(set(matched_keywords)),
            "matched_concepts": sorted(set(matched_concepts)),
            "missing_concepts": sorted(set(missing_concepts)),
            "forbidden_concepts": sorted(set(forbidden_concepts)),
            "hard_gate": dimension_hard_gate,
        }

    score = sum(dimension_scores) / len(dimension_scores)
    passed = passed_dimensions == len(expected)
    mode = (
        CORE_MODE_SUPPORTED
        if passed
        else CORE_MODE_CONFLICTED
        if hard_gate_failed
        else CORE_MODE_UNSUPPORTED
    )
    return passed, score, mode, {
        "content_confidence": round(confidence, 6),
        "minimum_content_confidence": round(minimum_confidence, 6),
        "dimensions": dimension_results,
    }


def _semantic_concept_present(
    haystack: str,
    concept: str,
    concepts: dict[str, object],
) -> bool:
    aliases = concepts.get(concept) or []
    return any(
        str(alias).strip().casefold() in haystack
        for alias in aliases
        if str(alias).strip()
    )


def _semantic_ranking_score(
    final_score: float,
    *,
    core_mode: str,
    core_evidence_score: float | None,
    settings: SimilarityPolicy,
) -> float:
    if core_mode != CORE_MODE_SUPPORTED:
        return final_score
    bonus = getattr(settings, "similarity_group_semantic_bonus", 0.06)
    return final_score + bonus * _clamp(core_evidence_score or 0.0)


def _candidate_ranking_score(
    candidate: ScoredCandidate,
    settings: SimilarityPolicy,
) -> float:
    if candidate.exact_match or candidate.core_evidence_mode == CORE_MODE_EXACT:
        return 2.0
    if candidate.ranking_score is not None:
        return candidate.ranking_score
    return _semantic_ranking_score(
        candidate.final_score,
        core_mode=candidate.core_evidence_mode,
        core_evidence_score=candidate.core_evidence_score,
        settings=settings,
    )


def _rule_value(rule: object, name: str, default: object = None) -> object:
    if isinstance(rule, dict):
        return rule.get(name, default)
    return getattr(rule, name, default)


def _rule_haystack(payload: dict[str, object], fields: list[str]) -> str:
    values: list[str] = []
    for field_name in fields:
        value = _field_value(payload, field_name)
        values.extend(_flatten_strings(value))
    return "\n".join(values).casefold()


def _flatten_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten_strings(item))
        return result
    if isinstance(value, dict):
        result = []
        for item in value.values():
            result.extend(_flatten_strings(item))
        return result
    return []


def _field_value(payload: dict[str, object], field_name: str) -> object:
    for key in _FIELD_ALIASES.get(field_name, (field_name,)):
        value = payload.get(key)
        if _has_value(value):
            if isinstance(value, dict) and "value" in value:
                return value.get("value")
            return value
    return None


def _has_value(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def _field_confidence(payload: dict[str, object], field_name: str) -> float:
    for key in _FIELD_ALIASES.get(field_name, (field_name,)):
        value = payload.get(key)
        if isinstance(value, dict) and "value" in value:
            confidence = value.get("confidence")
            if isinstance(confidence, (int, float)):
                return _clamp(float(confidence))
    for key in ("content_confidence", "confidence"):
        confidence = payload.get(key)
        if isinstance(confidence, (int, float)):
            return _clamp(float(confidence))
    return 0.7


def _text_evidence(
    field_name: str, left_value: object, right_value: object
) -> _ComparisonEvidence | None:
    left = _normalized_text(left_value)
    right = _normalized_text(right_value)
    if not left or not right:
        return None
    return _phrase_evidence(field_name, left, right)


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(value.strip().lower().split())


def _phrase_evidence(field_name: str, left: str, right: str) -> _ComparisonEvidence:
    if left == right:
        return _ComparisonEvidence(1.0, 1.0, "exact")
    left_concepts = _canonical_concepts(field_name, left)
    right_concepts = _canonical_concepts(field_name, right)
    if left_concepts and right_concepts:
        overlap = left_concepts & right_concepts
        if overlap:
            score = len(overlap) / len(left_concepts | right_concepts)
            return _ComparisonEvidence(score, 1.0, "canonical")
        if _is_canonical_conflict(field_name, left_concepts, right_concepts):
            return _ComparisonEvidence(0.0, 1.0, "canonical_conflict")
    if left in right or right in left:
        ratio = min(len(left), len(right)) / max(len(left), len(right))
        return _ComparisonEvidence(max(0.75, ratio), 1.0, "normalized")
    dice = _character_bigram_dice(left, right)
    if dice >= 0.35:
        return _ComparisonEvidence(dice, 1.0, "lexical")
    neutral = 0.0 if field_name == "ocr_text" else 0.5
    return _ComparisonEvidence(neutral, 0.0, "inconclusive")


def _canonical_concepts(field_name: str, value: str) -> set[str]:
    return {
        concept
        for concept, aliases in _CANONICAL_CONCEPTS.get(field_name, {}).items()
        if any(alias in value for alias in aliases)
    }


def _is_canonical_conflict(
    field_name: str, left: set[str], right: set[str]
) -> bool:
    conflicts = _MUTUALLY_EXCLUSIVE.get(field_name, set())
    return any(frozenset((left_item, right_item)) in conflicts for left_item in left for right_item in right)


def _character_bigram_dice(left: str, right: str) -> float:
    left_pairs = {left[index : index + 2] for index in range(max(0, len(left) - 1))}
    right_pairs = {right[index : index + 2] for index in range(max(0, len(right) - 1))}
    if not left_pairs or not right_pairs:
        return 0.0
    return 2 * len(left_pairs & right_pairs) / (len(left_pairs) + len(right_pairs))


def _longest_common_substring_length(left: str, right: str) -> int:
    previous = [0] * (len(right) + 1)
    longest = 0
    for left_char in left:
        current = [0]
        for index, right_char in enumerate(right, start=1):
            length = previous[index - 1] + 1 if left_char == right_char else 0
            current.append(length)
            longest = max(longest, length)
        previous = current
    return longest


def _list_evidence(
    field_name: str, left_values: list[str], right_values: list[str]
) -> _ComparisonEvidence | None:
    left = [_normalized_text(value) for value in left_values]
    right = [_normalized_text(value) for value in right_values]
    left = [value for value in left if value]
    right = [value for value in right if value]
    if not left or not right:
        return None
    source, target = (left, right) if len(left) <= len(right) else (right, left)
    matches = [
        max(
            (_phrase_evidence(field_name, value, other) for other in target),
            key=lambda evidence: (evidence.score, evidence.reliability),
        )
        for value in source
    ]
    return _ComparisonEvidence(
        score=sum(item.score for item in matches) / len(matches),
        reliability=sum(item.reliability for item in matches) / len(matches),
        method=_combined_method(matches),
    )


def _mapping_evidence(
    left_value: object, right_value: object
) -> _ComparisonEvidence | None:
    if not isinstance(left_value, dict) or not isinstance(right_value, dict):
        return None
    left = {
        _normalized_text(key): _string_values(value)
        for key, value in left_value.items()
        if _normalized_text(key)
    }
    right = {
        _normalized_text(key): _string_values(value)
        for key, value in right_value.items()
        if _normalized_text(key)
    }
    if not left or not right:
        return None
    comparisons = [
        evidence
        for key in set(left) & set(right)
        if (evidence := _list_evidence("mapping", left[key], right[key])) is not None
    ]
    if not comparisons:
        return None
    return _ComparisonEvidence(
        score=sum(item.score for item in comparisons) / len(comparisons),
        reliability=sum(item.reliability for item in comparisons) / len(comparisons),
        method=_combined_method(comparisons),
    )


def _combined_method(items: list[_ComparisonEvidence]) -> str:
    methods = {item.method for item in items}
    return methods.pop() if len(methods) == 1 else "mixed"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    return _string_list(value)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
