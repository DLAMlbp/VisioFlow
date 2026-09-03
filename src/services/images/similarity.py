from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from src.models.library_asset import LibraryAsset

SCORE_VERSION = "reliability_v4"

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
    similarity_review_threshold: float
    similarity_min_margin: float


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
) -> ScoredCandidate:
    evidence = feature_similarity_evidence(
        query_content,
        asset.analysis_json,
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
        score_version=score_version,
    )


def decide_similarity(
    *,
    candidates: list[ScoredCandidate],
    settings: SimilarityPolicy,
) -> SimilarityDecision:
    if not candidates:
        return unmatched_decision("无法识别")

    ranked, collapsed_same_image = _collapse_same_image_candidates(candidates)
    best = ranked[0]
    margin = best.final_score - ranked[1].final_score if len(ranked) > 1 else 1.0
    if best.final_score >= settings.similarity_auto_threshold:
        decision = "matched"
        message = (
            "已匹配到相同素材，已采用标签范围更广的素材组"
            if collapsed_same_image
            else "已通过图片向量与可靠内容证据匹配到相似素材"
        )
    elif best.final_score >= settings.similarity_review_threshold:
        decision = "pending_review"
        message = "图片与内容特征候选需要人工确认"
    else:
        decision = "unmatched"
        message = "未匹配到可信的图片与内容特征候选"

    serialized = [
        {
            "asset_id": candidate.asset.id,
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
        key = f"sha256:{sha256}" if sha256 else f"asset:{candidate.asset.id}"
        grouped.setdefault(key, []).append(candidate)

    collapsed = [
        max(
            group,
            key=lambda item: (
                _tag_scope_size(item.tags),
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


def _tag_scope_size(tags: list[str]) -> int:
    return len({tag.strip().casefold() for tag in tags if tag.strip()})


def apply_shadow_mode(
    decision: SimilarityDecision, *, enabled: bool
) -> SimilarityDecision:
    """Keep an automatic match review-only while preserving its audit scores."""
    if not enabled or decision.decision != "matched":
        return decision
    return SimilarityDecision(
        decision="pending_review",
        message="Shadow 模式：自动匹配结果等待人工确认",
        matched_asset_id=decision.matched_asset_id,
        tags=[],
        similarity_score=decision.similarity_score,
        feature_score=decision.feature_score,
        final_score=decision.final_score,
        feature_reliability=decision.feature_reliability,
        feature_coverage=decision.feature_coverage,
        candidate_margin=decision.candidate_margin,
        field_scores=decision.field_scores,
        score_version=decision.score_version,
        candidates=decision.candidates,
    )


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
