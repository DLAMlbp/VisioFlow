from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.services.storage.keys import validate_object_key


class LibraryAssetGroupCreate(BaseModel):
    tags: list[str] = Field(min_length=1, max_length=20)
    sort_order: int = Field(default=0, ge=0, le=100000)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return _normalize_tags(value)


class LibraryAssetGroupUpdate(BaseModel):
    tags: list[str] | None = Field(default=None, min_length=1, max_length=20)
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    status: Literal["active", "disabled"] | None = None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str] | None) -> list[str] | None:
        return _normalize_tags(value) if value is not None else None


class LibraryAssetGroupResponse(BaseModel):
    id: str
    tags: list[str]
    sort_order: int
    status: str
    asset_count: int = 0


class LibraryAssetCreate(BaseModel):
    object_key: str = Field(min_length=1, max_length=1024)
    group_id: str = Field(min_length=1, max_length=40)
    original_filename: str | None = Field(default=None, max_length=255)

    @field_validator("object_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        validate_object_key(value)
        return value


class LibraryAssetUpdate(BaseModel):
    group_id: str | None = Field(default=None, min_length=1, max_length=40)
    status: Literal["active", "disabled"] | None = None


class LibraryAssetResponse(BaseModel):
    id: str
    original_object_key: str
    thumbnail_object_key: str | None
    original_filename: str | None
    group_id: str
    tags: list[str]
    content_type: str | None
    width: int | None
    height: int | None
    status: str
    error_message: str | None
    analysis: dict[str, object] | None
    created_at: datetime


class LibraryAssetListResponse(BaseModel):
    total: int
    items: list[LibraryAssetResponse]


class LibraryFailedAssetReindexResponse(BaseModel):
    queued_count: int


class TagReviewResponse(BaseModel):
    image_id: str
    matched_asset_id: str | None
    tags: list[str]
    similarity_score: float | None
    feature_score: float | None
    final_score: float | None
    score_version: str
    feature_reliability: float | None
    feature_coverage: float | None
    candidate_margin: float | None
    field_scores: dict[str, dict[str, object]]
    decision: str
    message: str
    candidates: list[dict[str, object]]


class TagReviewDecisionRequest(BaseModel):
    decision: Literal["matched", "unmatched"]
    matched_asset_id: str | None = Field(default=None, max_length=40)


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_tag in tags:
        tag = raw_tag.strip()
        if not tag:
            raise ValueError("标签不能为空")
        if len(tag) > 80:
            raise ValueError("单个标签不能超过 80 个字符")
        key = tag.casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(tag)
    if not normalized:
        raise ValueError("至少需要一个标签")
    return normalized
