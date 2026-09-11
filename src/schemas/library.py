from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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


class LibraryAssetBulkDeleteRequest(BaseModel):
    asset_ids: list[str] = Field(default_factory=list, max_length=10000)
    delete_all: bool = False
    group_id: str | None = Field(default=None, min_length=1, max_length=40)

    @field_validator("asset_ids")
    @classmethod
    def normalize_asset_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(asset_id.strip() for asset_id in value if asset_id.strip()))

    @model_validator(mode="after")
    def validate_delete_scope(self) -> "LibraryAssetBulkDeleteRequest":
        if self.delete_all and self.asset_ids:
            raise ValueError("删除全部时不能同时指定素材")
        if not self.delete_all and not self.asset_ids:
            raise ValueError("请选择要删除的素材")
        if not self.delete_all and self.group_id is not None:
            raise ValueError("按素材删除时不能指定素材组")
        return self


class LibraryAssetBulkDeleteResponse(BaseModel):
    deleted_count: int
    failed_count: int
    failed_asset_ids: list[str]


class LibraryFailedAssetReindexResponse(BaseModel):
    queued_count: int


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
