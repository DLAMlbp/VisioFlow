from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

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
    BEAUTIFY_PLANNING = "beautify_planning"
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
    client_object_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("object_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        try:
            validate_object_key(value)
        except Exception as exc:
            raise ValueError("object_key 不合法") from exc
        return value


class FilterRoutingPolicy(BaseModel):
    insufficient_evidence_policy: Literal["reject", "route_non_completed"] = (
        "route_non_completed"
    )
    low_confidence_policy: Literal["continue_with_review", "reject"] = (
        "continue_with_review"
    )


class CompletionFilterRoute(BaseModel):
    completion_profile: str = Field(min_length=1, max_length=80)
    completed_filter_profile: str = Field(min_length=1, max_length=80)
    non_completed_filter_profile: str = Field(min_length=1, max_length=80)
    policy: FilterRoutingPolicy = Field(default_factory=FilterRoutingPolicy)

    @model_validator(mode="after")
    def require_distinct_branches(self):
        if self.completed_filter_profile == self.non_completed_filter_profile:
            raise ValueError("完工与非完工过滤标准不能相同")
        return self


class CreateImageJobRequest(BaseModel):
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
    images: list[CreateJobImage] = Field(min_length=1)
    callback_url: HttpUrl | None = None
    callback_contract: Literal["native_v1", "customer_v1"] = "native_v1"

    @model_validator(mode="after")
    def require_processing_configuration(self):
        if not (self.filter_enabled and self.beautify_enabled and self.similarity_enabled):
            raise ValueError("正式模式固定执行完工分类、分支过滤、过滤后美化和素材库匹配")
        if len(set(self.processing_standards)) != len(self.processing_standards):
            raise ValueError("过滤标准不能重复")
        return self


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
    source: Literal["library", "legacy_ai"] | None = None
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
    tags: list[str] = Field(default_factory=list)
    matched_asset_id: str | None = None
    similarity: float | None = Field(default=None, ge=0, le=1)
    feature_score: float | None = Field(default=None, ge=0, le=1)
    final_score: float | None = Field(default=None, ge=0, le=1)
    auto_threshold: float | None = Field(default=None, ge=0, le=1)
    review_threshold: float | None = Field(default=None, ge=0, le=1)
    message: str


class ImageAuditDimensionResponse(BaseModel):
    dimension: str
    passed: bool
    reason: str


class ImageCompletionResponse(BaseModel):
    label: Literal["completed", "non_completed"]
    subtype: Literal[
        "completed",
        "construction",
        "insufficient_evidence",
        "invalid_or_irrelevant",
    ]
    confidence: float = Field(ge=0, le=1)
    reason: str
    reason_codes: list[str] = Field(default_factory=list)
    review_required: bool = False


class ImageClassificationResponse(BaseModel):
    class ContentAnalysis(BaseModel):
        summary: str
        content_type: str
        scene: str
        spaces: list[str] = Field(default_factory=list)
        view: str
        subjects: list[str] = Field(default_factory=list)
        objects: list[str] = Field(default_factory=list)
        visible_conditions: list[str] = Field(default_factory=list)
        attributes: dict[str, list[str]] = Field(default_factory=dict)
        supporting_evidence: list[str] = Field(default_factory=list)
        conflicting_evidence: list[str] = Field(default_factory=list)
        missing_evidence: list[str] = Field(default_factory=list)
        uncertainties: list[str] = Field(default_factory=list)
        ocr_text: list[str] = Field(default_factory=list)
        confidence: float = Field(ge=0, le=1)

    standard_id: str
    standard_name: str
    confidence: float = Field(ge=0, le=1)
    reason: str
    review_required: bool = False
    content_analysis: ContentAnalysis | None = None


class BeautifyAcceptanceCheckResponse(BaseModel):
    name: Literal["exposure", "color", "noise", "sharpening"]
    passed: bool
    before: dict[str, float] = Field(default_factory=dict)
    after: dict[str, float] = Field(default_factory=dict)
    reason: str


class BeautifyAcceptanceResponse(BaseModel):
    status: Literal["passed", "fallback", "failed"]
    checks: list[BeautifyAcceptanceCheckResponse] = Field(default_factory=list)
    fallback_reason: str | None = None


class ImageRedactionResponse(BaseModel):
    watermark: dict[str, object] = Field(default_factory=dict)
    logos: dict[str, object] = Field(default_factory=dict)


class UpdateLogoRedactionRequest(BaseModel):
    """Final pixel boxes selected by an operator during result review."""

    boxes: list[tuple[int, int, int, int]] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_boxes(self):
        for x0, y0, x1, y1 in self.boxes:
            if min(x0, y0) < 0 or x1 <= x0 or y1 <= y0:
                raise ValueError("Logo 框必须使用非负且非空的 x0,y0,x1,y1 像素坐标")
        return self


class UpdateLogoRedactionResponse(BaseModel):
    image_id: str
    status: Literal["manual_applied", "manual_cleared"]
    boxes: list[tuple[int, int, int, int]] = Field(default_factory=list)
    image_size: tuple[int, int]
    detections: int
    source: Literal["manual_review"] = "manual_review"


class ImageBeautifyResponse(BaseModel):
    status: str
    needed: bool | None = None
    reason: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    planned_parameters: dict[str, object] = Field(default_factory=dict)
    effective_parameters: dict[str, object] = Field(default_factory=dict)
    corrections: list[str] = Field(default_factory=list)
    preview_attempts: int = 0
    acceptance: BeautifyAcceptanceResponse | None = None
    redaction: ImageRedactionResponse | None = None


class ImageJobResultItemResponse(BaseModel):
    image_id: str
    client_object_key: str | None = None
    decision: ImageItemStatus
    score: float | None = None
    original_object_key: str
    enhanced_object_key: str | None = None
    original_preview_object_key: str | None = None
    enhanced_preview_object_key: str | None = None
    files_expired: bool = False
    reject_codes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    metrics: ImageMetricsResponse | None = None
    enhanced_metrics: ImageMetricsResponse | None = None
    ai_tags: ImageAITagsResponse | None = None
    library_tags: ImageSimilarityResultResponse | None = None
    tagging_result: ImageSimilarityResultResponse | None = None
    processing_standard_id: str | None = None
    processing_standard_name: str | None = None
    activation_reason: str | None = None
    audit_dimensions: list[ImageAuditDimensionResponse] = Field(default_factory=list)
    completion: ImageCompletionResponse | None = None
    classification: ImageClassificationResponse | None = None
    beautify: ImageBeautifyResponse | None = None
    routed_filter_profile_id: str | None = None
    routed_filter_profile_version: int | None = None
    pipeline_stage: str = "unknown"
    classification_status: str | None = None
    filter_status: str | None = None
    beautify_status: str | None = None
    analysis_status: str | None = None
    embedding_status: str | None = None
    match_status: str | None = None


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
