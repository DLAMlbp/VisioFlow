from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl, field_validator

from src.services.storage.keys import validate_object_key


class JobStatus(StrEnum):
    CREATED = "created"
    UPLOADING = "uploading"
    QUEUED = "queued"
    PROCESSING = "processing"
    ANALYZING = "analyzing"
    RANKING = "ranking"
    ENHANCING = "enhancing"
    TAGGING = "tagging"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImageItemStatus(StrEnum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    FILTERED = "filtered"
    ENHANCING = "enhancing"
    ENHANCED = "enhanced"
    TAGGING = "tagging"
    REJECTED = "rejected"
    SELECTED = "selected"
    FAILED = "failed"
    NOT_SELECTED = "not_selected"
    CANCELLED = "cancelled"


class CreateJobImage(BaseModel):
    object_key: str = Field(min_length=1, max_length=1024)

    @field_validator("object_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        try:
            validate_object_key(value)
        except Exception as exc:
            raise ValueError("object_key 不合法") from exc
        return value


class CreateImageJobRequest(BaseModel):
    filter_profile: str = Field(default="renovation_submission_v1", min_length=1, max_length=80)
    beautify_profile: str = Field(default="renovation_natural_v1", min_length=1, max_length=80)
    similarity_profile: str = Field(default="library_similarity_v2", min_length=1, max_length=80)
    enhance_level: int = Field(default=1, ge=0, le=2)
    max_selected: int = Field(default=10, ge=1)
    images: list[CreateJobImage] = Field(min_length=1)
    callback_url: HttpUrl | None = None


class CreateImageJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    total: int


class ImageJobProgressResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    total: int
    processed: int
    selected: int
    rejected: int
    not_selected: int = 0
    tagging: int = 0
    stage_counts: dict[str, int] = Field(default_factory=dict)


class ImageJobHistoryItemResponse(BaseModel):
    job_id: str
    status: JobStatus
    total: int
    processed: int
    selected: int
    rejected: int
    not_selected: int = 0
    ai_tagging_model: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ImageJobHistoryResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ImageJobHistoryItemResponse]


class ImageMetricsResponse(BaseModel):
    sharpness: float
    exposure: float
    contrast: float
    noise: float


class ImageAITagsResponse(BaseModel):
    status: str
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    categories: dict[str, list[str]] = Field(default_factory=dict)
    candidate_tags: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    risks: list[str] = Field(default_factory=list)
    source_object_key: str | None = None
    error_message: str | None = None


class ImageSimilarityResultResponse(BaseModel):
    decision: str
    tag_path: list[str] = Field(default_factory=list)
    matched_asset_id: str | None = None
    similarity: float | None = Field(default=None, ge=0, le=1)
    final_score: float | None = Field(default=None, ge=0, le=1)
    message: str


class ImageJobResultItemResponse(BaseModel):
    image_id: str
    decision: ImageItemStatus
    score: float | None = None
    original_object_key: str
    enhanced_object_key: str | None = None
    files_expired: bool = False
    reject_codes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    metrics: ImageMetricsResponse | None = None
    enhanced_metrics: ImageMetricsResponse | None = None
    ai_tags: ImageAITagsResponse | None = None
    tagging_result: ImageSimilarityResultResponse | None = None


class ImageJobResultsResponse(BaseModel):
    job_id: str
    total: int
    selected: int
    rejected: int
    not_selected: int = 0
    result_total: int = 0
    limit: int = 50
    offset: int = 0
    images: list[ImageJobResultItemResponse]
