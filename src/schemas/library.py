from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.services.storage.keys import validate_object_key


class LibraryTagNodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = Field(default=None, max_length=40)
    sort_order: int = Field(default=0, ge=0, le=100000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("标签名称不能为空")
        return value


class LibraryTagNodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    status: Literal["active", "disabled"] | None = None


class LibraryTagNodeResponse(BaseModel):
    id: str
    parent_id: str | None
    name: str
    depth: int
    sort_order: int
    status: str
    asset_count: int = 0
    children: list["LibraryTagNodeResponse"] = Field(default_factory=list)


class LibraryAssetCreate(BaseModel):
    object_key: str = Field(min_length=1, max_length=1024)
    leaf_tag_node_id: str = Field(min_length=1, max_length=40)
    original_filename: str | None = Field(default=None, max_length=255)

    @field_validator("object_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        validate_object_key(value)
        return value


class LibraryAssetUpdate(BaseModel):
    leaf_tag_node_id: str | None = Field(default=None, min_length=1, max_length=40)
    status: Literal["active", "disabled"] | None = None


class LibraryAssetResponse(BaseModel):
    id: str
    original_object_key: str
    original_filename: str | None
    leaf_tag_node_id: str
    tag_path: list[str]
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


class TagReviewResponse(BaseModel):
    image_id: str
    matched_asset_id: str | None
    tag_path: list[str]
    similarity_score: float | None
    feature_score: float | None
    final_score: float | None
    decision: str
    message: str
    candidates: list[dict[str, object]]


class TagReviewDecisionRequest(BaseModel):
    decision: Literal["matched", "unmatched"]
    matched_asset_id: str | None = Field(default=None, max_length=40)
