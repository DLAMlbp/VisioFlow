from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class UploadBatchFile(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    file_size: int = Field(gt=0)


class CreateUploadBatchRequest(BaseModel):
    filter_profile: str = Field(min_length=1, max_length=80)
    beautify_profile: str = Field(min_length=1, max_length=80)
    similarity_profile: str = Field(default="library_similarity_v2", min_length=1, max_length=80)
    enhance_level: int = Field(default=1, ge=0, le=2)
    max_selected: int | None = Field(default=None, ge=1)
    callback_url: HttpUrl | None = None
    files: list[UploadBatchFile] = Field(min_length=1, max_length=500)


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
