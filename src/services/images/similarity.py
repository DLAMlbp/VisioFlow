from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.models.library_asset import LibraryAsset


class SimilarityPolicy(Protocol):
    similarity_auto_threshold: float
    similarity_review_threshold: float
    similarity_min_margin: float


@dataclass(frozen=True)
class ScoredCandidate:
    asset: LibraryAsset
    tag_path: list[str]
    similarity_score: float
    feature_score: float
    final_score: float


@dataclass(frozen=True)
class SimilarityDecision:
    decision: str
    message: str
    matched_asset_id: str | None
    tag_path: list[str]
    similarity_score: float | None
    feature_score: float | None
    final_score: float | None
    candidates: list[dict[str, object]]


def feature_similarity(
    query: dict[str, object] | None, candidate: dict[str, object] | None
) -> float:
    if not query or not candidate:
        return 0.0
    scores: list[float] = []
    for field in ("scene", "space", "condition", "content_type", "view"):
        left = _normalized_text(query.get(field))
        right = _normalized_text(candidate.get(field))
        if left and right:
            scores.append(_field_similarity(field, left, right))
    left_subjects = {_normalized_text(value) for value in _string_list(query.get("subjects"))}
    right_subjects = {_normalized_text(value) for value in _string_list(candidate.get("subjects"))}
    left_subjects.discard("")
    right_subjects.discard("")
    if left_subjects and right_subjects:
        scores.append(len(left_subjects & right_subjects) / min(len(left_subjects), len(right_subjects)))
    return sum(scores) / len(scores) if scores else 0.0


def decide_similarity(
    *,
    candidates: list[ScoredCandidate],
    settings: SimilarityPolicy,
) -> SimilarityDecision:
    if not candidates:
        return unmatched_decision("无法识别")

    ranked = sorted(
        candidates,
        key=lambda item: (item.similarity_score, item.final_score),
        reverse=True,
    )
    best = ranked[0]
    if (
        best.similarity_score >= settings.similarity_auto_threshold
        and best.final_score >= settings.similarity_auto_threshold
    ):
        decision = "matched"
        message = "已匹配到相似图片素材"
    else:
        decision = "pending_review"
        message = "无法识别，等待人工复核"

    serialized = [
        {
            "asset_id": candidate.asset.id,
            "tag_path": candidate.tag_path,
            "similarity_score": round(candidate.similarity_score, 4),
            "feature_score": round(candidate.feature_score, 4),
            "final_score": round(candidate.final_score, 4),
        }
        for candidate in ranked[:10]
    ]
    if decision == "unmatched":
        return SimilarityDecision(
            decision=decision,
            message=message,
            matched_asset_id=None,
            tag_path=[],
            similarity_score=best.similarity_score,
            feature_score=best.feature_score,
            final_score=best.final_score,
            candidates=serialized,
        )
    return SimilarityDecision(
        decision=decision,
        message=message,
        matched_asset_id=best.asset.id,
        tag_path=best.tag_path,
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
        tag_path=[],
        similarity_score=None,
        feature_score=None,
        final_score=None,
        candidates=[],
    )


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(value.strip().lower().split())


def _field_similarity(field: str, left: str, right: str) -> float:
    if left == right:
        return 1.0
    if field == "condition":
        left_stage = _condition_stage(left)
        right_stage = _condition_stage(right)
        if left_stage and right_stage:
            return 1.0 if left_stage == right_stage else 0.0
    if field == "view":
        left_view = _view_type(left)
        right_view = _view_type(right)
        if left_view and right_view:
            return 1.0 if left_view == right_view else 0.0
    if field == "space":
        left_spaces = _space_types(left)
        right_spaces = _space_types(right)
        if left_spaces and right_spaces:
            overlap = len(left_spaces & right_spaces) / min(len(left_spaces), len(right_spaces))
            if overlap:
                return max(overlap, _phrase_similarity(left, right))
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


def _condition_stage(value: str) -> str:
    groups = {
        "before": ("装修前", "施工前", "待装修", "毛坯"),
        "during": ("施工中", "装修中", "施工现场", "改造中"),
        "completed": ("装修完成", "已装修", "完工", "竣工", "精装"),
    }
    for stage, aliases in groups.items():
        if any(alias in value for alias in aliases):
            return stage
    return ""


def _view_type(value: str) -> str:
    groups = {
        "panorama": ("全景", "整体"),
        "medium": ("中景",),
        "closeup": ("特写", "近景", "细节", "局部"),
    }
    for view_type, aliases in groups.items():
        if any(alias in value for alias in aliases):
            return view_type
    return ""


def _space_types(value: str) -> set[str]:
    spaces = {
        name
        for name in (
            "客厅",
            "餐厅",
            "玄关",
            "厨房",
            "卧室",
            "书房",
            "阳台",
            "卫生间",
            "厕所",
            "过道",
            "楼梯",
            "外立面",
        )
        if name in value
    }
    if "客餐厅" in value:
        spaces.update({"客厅", "餐厅"})
    if "卫浴" in value:
        spaces.add("卫生间")
    return spaces


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
