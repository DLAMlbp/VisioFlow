from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.models.library_asset import LibraryAsset


class SimilarityPolicy(Protocol):
    similarity_image_weight: float
    similarity_feature_weight: float
    similarity_auto_threshold: float
    similarity_review_threshold: float
    similarity_min_margin: float


@dataclass(frozen=True)
class ScoredCandidate:
    asset: LibraryAsset
    tags: list[str]
    similarity_score: float
    feature_score: float | None
    final_score: float


@dataclass(frozen=True)
class SimilarityDecision:
    decision: str
    message: str
    matched_asset_id: str | None
    tags: list[str]
    similarity_score: float | None
    feature_score: float | None
    final_score: float | None
    candidates: list[dict[str, object]]


def feature_similarity(
    query: dict[str, object] | None, candidate: dict[str, object] | None
) -> float | None:
    if not query or not candidate:
        return None
    scores: list[float] = []
    for field in ("scene", "space", "condition", "content_type", "view"):
        left = _normalized_text(query.get(field))
        right = _normalized_text(candidate.get(field))
        if left and right:
            scores.append(_field_similarity(field, left, right))
    for field in ("subjects", "objects", "ocr_text"):
        list_score = _list_similarity(
            _string_list(query.get(field)), _string_list(candidate.get(field))
        )
        if list_score is not None:
            scores.append(list_score)
    for field in ("attributes", "features"):
        mapping_score = _mapping_similarity(query.get(field), candidate.get(field))
        if mapping_score is not None:
            scores.append(mapping_score)
    return sum(scores) / len(scores) if scores else 0.0


def combined_similarity_score(
    *,
    similarity_score: float,
    feature_score: float | None,
    settings: SimilarityPolicy,
) -> float:
    if feature_score is None:
        return similarity_score
    total_weight = settings.similarity_image_weight + settings.similarity_feature_weight
    if total_weight <= 0:
        return similarity_score
    return (
        similarity_score * settings.similarity_image_weight
        + feature_score * settings.similarity_feature_weight
    ) / total_weight


def decide_similarity(
    *,
    candidates: list[ScoredCandidate],
    settings: SimilarityPolicy,
) -> SimilarityDecision:
    if not candidates:
        return unmatched_decision("无法识别")

    ranked, collapsed_same_image = _collapse_same_image_candidates(candidates)
    best = ranked[0]
    margin = (
        best.final_score - ranked[1].final_score
        if len(ranked) > 1
        else 1.0
    )
    content_features_ready = (
        settings.similarity_feature_weight <= 0 or best.feature_score is not None
    )
    if (
        content_features_ready
        and
        best.similarity_score >= settings.similarity_auto_threshold
        and best.final_score >= settings.similarity_auto_threshold
        and margin >= settings.similarity_min_margin
    ):
        decision = "matched"
        message = (
            "已匹配到相同素材，已采用标签范围更广的素材组"
            if collapsed_same_image
            else "已通过图片向量与内容特征匹配到相似素材"
        )
    elif (
        best.similarity_score >= settings.similarity_review_threshold
        or best.final_score >= settings.similarity_review_threshold
    ):
        decision = "pending_review"
        message = (
            "内容特征暂不可用，候选素材需要人工确认"
            if best.feature_score is None and settings.similarity_feature_weight > 0
            else "图片与内容特征候选需要人工确认"
        )
    else:
        decision = "unmatched"
        message = "未匹配到可信的图片与内容特征候选"

    serialized = [
        {
            "asset_id": candidate.asset.id,
            "original_filename": candidate.asset.original_filename,
            "preview_object_key": (
                candidate.asset.thumbnail_object_key
                or candidate.asset.original_object_key
            ),
            "tags": candidate.tags,
            "similarity_score": round(candidate.similarity_score, 4),
            "feature_score": (
                round(candidate.feature_score, 4)
                if candidate.feature_score is not None
                else None
            ),
            "final_score": round(candidate.final_score, 4),
        }
        for candidate in ranked[:10]
    ]
    if decision == "unmatched":
        return SimilarityDecision(
            decision=decision,
            message=message,
            matched_asset_id=None,
            tags=[],
            similarity_score=best.similarity_score,
            feature_score=best.feature_score,
            final_score=best.final_score,
            candidates=serialized,
        )
    if decision == "pending_review":
        return SimilarityDecision(
            decision=decision,
            message=message,
            matched_asset_id=best.asset.id,
            tags=[],
            similarity_score=best.similarity_score,
            feature_score=best.feature_score,
            final_score=best.final_score,
            candidates=serialized,
        )
    return SimilarityDecision(
        decision=decision,
        message=message,
        matched_asset_id=best.asset.id,
        tags=best.tags,
        similarity_score=best.similarity_score,
        feature_score=best.feature_score,
        final_score=best.final_score,
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
        candidates=decision.candidates,
    )


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(value.strip().lower().split())


def _field_similarity(field: str, left: str, right: str) -> float:
    del field
    if left == right:
        return 1.0
    return _phrase_similarity(left, right)


def _phrase_similarity(left: str, right: str) -> float:
    if left in right or right in left:
        return min(len(left), len(right)) / max(len(left), len(right))
    common_length = _longest_common_substring_length(left, right)
    score = common_length / min(len(left), len(right))
    return score if score >= 0.4 else 0.0


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


def _list_similarity(left_values: list[str], right_values: list[str]) -> float | None:
    left = [_normalized_text(value) for value in left_values]
    right = [_normalized_text(value) for value in right_values]
    left = [value for value in left if value]
    right = [value for value in right if value]
    if not left or not right:
        return None
    source, target = (left, right) if len(left) <= len(right) else (right, left)
    return sum(max(_phrase_similarity(value, other) for other in target) for value in source) / len(source)


def _mapping_similarity(left_value: object, right_value: object) -> float | None:
    if not isinstance(left_value, dict) or not isinstance(right_value, dict):
        return None
    left = {_normalized_text(key): _string_values(value) for key, value in left_value.items()}
    right = {_normalized_text(key): _string_values(value) for key, value in right_value.items()}
    common_keys = (set(left) & set(right)) - {""}
    scores = [
        score
        for key in common_keys
        if (score := _list_similarity(left[key], right[key])) is not None
    ]
    return sum(scores) / len(scores) if scores else None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    return _string_list(value)
