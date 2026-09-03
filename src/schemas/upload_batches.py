from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator

from src.schemas.jobs import CompletionFilterRoute


class UploadBatchFile(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    file_size: int = Field(gt=0)


class CreateUploadBatchRequest(BaseModel):
    filter_route: CompletionFilterRoute | None = None
    processing_standards: list[str] = Field(default_factory=list, max_length=20)
    filter_profile: str | None = Field(default=None, min_length=1, max_length=80)
    beautify_profile: str | None = Field(default=None, min_length=1, max_length=80)
    redaction_profile: str | None = Field(default=None, min_length=1, max_length=80)
    filter_enabled: bool = True
    beautify_enabled: bool = True
    similarity_enabled: bool = True
    similarity_profile: str = Field(default="library_similarity_v2", min_length=1, max_length=80)
    unmatched_standard_policy: Literal["reject"] = "reject"
    enhance_level: int = Field(default=1, ge=0, le=2)
    max_selected: int | None = Field(default=None, ge=1)
    callback_url: HttpUrl | None = None
    files: list[UploadBatchFile] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_processing_configuration(self):
        if not (self.filter_enabled and self.beautify_enabled and self.similarity_enabled):
            raise ValueError("正式模式固定执行完工分类、分支过滤、过滤后美化和素材库匹配")
        if len(set(self.processing_standards)) != len(self.processing_standards):
            raise ValueError("过滤标准不能重复")
        if not self.beautify_profile:
            raise ValueError("请选择独立的美化标准")
        return self


class UploadBatchItemResponse(BaseModel):
    id: str
    filename: str
    content_type: str
    file_size: int
    object_key: str
    upload_url: str


class CreateUploadBatchResponse(BaseModel):
    batch_id: str
    status: str
    expires_at: datetime
    items: list[UploadBatchItemResponse]


class CompleteUploadBatchRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=500)


class CompleteUploadBatchResponse(BaseModel):
    batch_id: str
    job_id: str
    status: str
    total: int
