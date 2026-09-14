from pathlib import Path
from typing import Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, ValidationError, model_validator

from src.core.config import Settings


class ProfileNotFoundError(Exception):
    pass


class HardRulesProfile(BaseModel):
    min_width: int = Field(ge=1)
    min_height: int = Field(ge=1)
    max_width: int = Field(ge=1)
    max_height: int = Field(ge=1)
    min_edge_variance: float = Field(ge=0)
    max_overexposed_ratio: float = Field(ge=0, le=1)
    max_underexposed_ratio: float = Field(ge=0, le=1)
    reject_underexposed_ratio: float = Field(default=0.85, ge=0, le=1)
    min_visible_content_ratio: float = Field(default=0.03, ge=0, le=1)
    min_quality_score: float = Field(ge=0, le=100)
    max_solid_color_stddev: float = Field(ge=0)
    min_sharpness_score: float = Field(default=92, ge=0, le=100)
    min_exposure_score: float = Field(default=93, ge=0, le=100)
    min_contrast_score: float = Field(default=88, ge=0, le=100)
    min_noise_score: float = Field(default=90, ge=0, le=100)
    duplicate_hamming_distance: int = Field(default=5, ge=0, le=64)
    no_enhancement_score: float = Field(default=98, ge=0, le=100)
    ideal_brightness_min: float = Field(default=118, ge=0, le=255)
    ideal_brightness_max: float = Field(default=138, ge=0, le=255)


class EvidenceRulesProfile(BaseModel):
    require_scene_classification: bool = False
    stages: list[str] = Field(default_factory=list)
    required_views: list[str] = Field(default_factory=list)


class FilterProfile(BaseModel):
    id: str
    version: int = Field(ge=1)
    description: str
    # Kept only to read snapshots created before filtering became AI-driven.
    # Runtime filtering no longer executes these business thresholds locally.
    hard_rules: HardRulesProfile | None = None
    evidence_rules: EvidenceRulesProfile | None = None


class WatermarkRemovalConfig(BaseModel):
    """Deterministic removal rules for the supported bottom-left overlay."""

    enabled: bool = False
    mode: Literal["dangjia_bottom_left"] = "dangjia_bottom_left"
    roi: tuple[float, float, float, float] = (0.0, 0.84, 0.48, 1.0)
    backend: Literal[
        "deblend",
        "deblend_then_telea",
        "deblend_then_migan",
        "deblend_then_lama",
        "migan",
        "lama",
    ] = (
        "deblend_then_lama"
    )
    preserve_outside_roi: bool = True
    # This ratio is measured inside the protected ROI. Even at the default 0.50,
    # at most 4% of the full image can change because the ROI itself is only 8%.
    max_modified_ratio: float = Field(default=0.50, gt=0, le=0.65)
    detection_threshold: float = Field(default=0.38, ge=0, le=1)
    inpaint_radius: int = Field(default=3, ge=1, le=12)
    roi_ocr_enabled: bool = True
    allow_during_filter: bool = True
    post_action: Literal["remove", "keep"] = "remove"

    @model_validator(mode="after")
    def validate_roi(self):
        x0, y0, x1, y1 = self.roi
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("watermark ROI must be normalized as x0,y0,x1,y1")
        if self.preserve_outside_roi and (x1 - x0) * (y1 - y0) > 0.25:
            raise ValueError("protected watermark ROI cannot cover more than 25% of the image")
        return self


class LogoMosaicConfig(BaseModel):
    """Single-brand logo detection and configured delivery-time cover action."""

    enabled: bool = False
    targets: list[Literal["dangjia_logo"]] = Field(
        default_factory=lambda: ["dangjia_logo"]
    )
    confidence: float = Field(default=0.45, ge=0.05, le=0.99)
    nms_iou: float = Field(default=0.50, ge=0.1, le=0.9)
    box_expansion: float = Field(default=0.08, ge=0, le=0.5)
    mosaic_block_ratio: float = Field(default=0.16, ge=0.02, le=0.5)
    include_product_logos: bool = False
    action: Literal["mosaic", "overlay_asset"] = "mosaic"
    target_component: Literal["full_logo", "app_text"] = "app_text"
    overlay_asset_id: Literal["xiaodang_v1", "xiaodang_cutout_v1"] = (
        "xiaodang_cutout_v1"
    )
    overlay_scale: float = Field(default=1.12, ge=1.0, le=2.0)


class BrandedGroundFilmConfig(BaseModel):
    """Reject images dominated by visible Dangjia-branded floor protection film."""

    enabled: bool = False
    brand: Literal["dangjia_app"] = "dangjia_app"
    reject_coverage_gte: float = Field(default=0.75, ge=0.05, le=0.98)
    review_margin: float = Field(default=0.05, ge=0.0, le=0.25)
    min_confidence: float = Field(default=0.70, ge=0.0, le=1.0)
    uncertain_action: Literal["manual_review", "pass"] = "manual_review"


class RedactionProfile(BaseModel):
    """Versioned watermark, logo-cover and branded-ground-film policy."""

    id: str
    version: int = Field(ge=1)
    description: str
    watermark: WatermarkRemovalConfig = Field(default_factory=WatermarkRemovalConfig)
    logo: LogoMosaicConfig = Field(default_factory=LogoMosaicConfig)
    branded_ground_film: BrandedGroundFilmConfig = Field(
        default_factory=BrandedGroundFilmConfig
    )


class BeautifyProfile(BaseModel):
    id: str
    version: int = Field(ge=1)
    description: str
    brightness: float = Field(ge=0.5, le=1.5)
    contrast: float = Field(ge=0.5, le=1.5)
    color: float = Field(ge=0.5, le=1.5)
    sharpness: float = Field(ge=0.5, le=2.0)
    auto_white_balance: bool = True
    white_balance_strength: float = Field(default=0.65, ge=0, le=1)
    shadow_lift: float = Field(default=0.1, ge=0, le=0.35)
    highlight_recovery: float = Field(default=0.1, ge=0, le=0.35)
    denoise_strength: float = Field(default=0.16, ge=0, le=0.5)
    local_tone_strength: float = Field(default=0.18, ge=0, le=0.5)
    local_tone_clip_limit: float = Field(default=1.5, ge=1, le=3)
    glare_reduction_strength: float = Field(default=0.2, ge=0, le=0.5)
    local_clarity_strength: float = Field(default=0.2, ge=0, le=0.5)
    auto_straighten: bool = True
    max_straighten_degrees: float = Field(default=3, gt=0, le=12)
    min_output_long_side: int = Field(default=2048, ge=1, le=8192)
    jpeg_quality: int = Field(ge=60, le=100)
    watermark_removal: WatermarkRemovalConfig = Field(
        default_factory=WatermarkRemovalConfig
    )
    logo_mosaic: LogoMosaicConfig = Field(default_factory=LogoMosaicConfig)


class ProcessingStandard(BaseModel):
    """One classification rule paired with exactly one filter rule."""

    id: str
    name: str = ""
    version: int = Field(ge=1)
    description: str
    classification_rule: str = Field(
        min_length=3,
        max_length=2000,
        validation_alias=AliasChoices("classification_rule", "activation_rule"),
    )
    filter_rule: str = Field(min_length=3, max_length=2000)
    priority: int = Field(default=100, ge=0, le=10000)
    is_fallback: bool = False

    @property
    def activation_rule(self) -> str:
        """Read snapshots created before classification_rule was introduced."""
        return self.classification_rule


class CompletionProfile(BaseModel):
    """Versioned instructions used only for renovation completion classification."""

    id: str
    version: int = Field(ge=1)
    description: str


class SimilarityProfile(BaseModel):
    id: str
    version: int = Field(ge=1)
    description: str
    similarity_candidate_limit: int = Field(ge=1, le=100)
    similarity_image_weight: float = Field(ge=0, le=1)
    similarity_feature_weight: float = Field(ge=0, le=1)
    similarity_dynamic_weighting_enabled: bool = False
    similarity_min_content_weight: float = Field(default=0.0, ge=0, le=1)
    similarity_max_content_weight: float = Field(default=0.30, ge=0, le=1)
    similarity_field_weights: dict[str, float] = Field(
        default_factory=lambda: {
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
    )
    similarity_auto_threshold: float = Field(ge=0, le=1)
    similarity_feature_auto_threshold: float = Field(default=0.75, ge=0, le=1)
    similarity_review_threshold: float = Field(ge=0, le=1)
    similarity_min_margin: float = Field(ge=0, le=1)
    similarity_group_matching_enabled: bool = False
    similarity_group_visual_best_weight: float = Field(default=0.65, ge=0, le=1)
    similarity_group_max_prototypes: int = Field(default=12, ge=1, le=20)
    similarity_group_support_threshold: float = Field(default=0.78, ge=0, le=1)
    similarity_group_content_refine_limit: int = Field(default=16, ge=1, le=100)
    similarity_group_min_content_confidence: float = Field(default=0.60, ge=0, le=1)
    similarity_group_semantic_bonus: float = Field(default=0.06, ge=0, le=0.25)
    similarity_group_unsupported_auto_threshold: float = Field(
        default=0.85, ge=0, le=1
    )
    similarity_group_fallback_auto_enabled: bool = False
    similarity_group_fallback_auto_threshold: float = Field(default=0.92, ge=0, le=1)
    similarity_group_fallback_min_margin: float = Field(default=0.12, ge=0, le=1)
    similarity_group_fallback_min_support: int = Field(default=2, ge=1, le=12)
    similarity_group_fallback_min_feature_score: float = Field(default=0.70, ge=0, le=1)
    similarity_group_fallback_min_feature_strength: float = Field(
        default=0.10, ge=0, le=1
    )
    similarity_semantic_concepts: dict[str, list[str]] = Field(default_factory=dict)
    similarity_group_tag_rules: dict[str, "SimilarityGroupTagRule"] = Field(
        default_factory=dict
    )

    def model_post_init(self, __context: object, /) -> None:
        if self.similarity_min_content_weight > self.similarity_max_content_weight:
            raise ValueError("内容特征最小权重不能超过最大权重")
        if not self.similarity_field_weights or any(
            weight < 0 or weight > 1 for weight in self.similarity_field_weights.values()
        ):
            raise ValueError("内容特征字段权重必须是 0 到 1 之间的非空映射")
        if any(not aliases for aliases in self.similarity_semantic_concepts.values()):
            raise ValueError("语义概念必须至少配置一个可识别表达")
        unknown_concepts = {
            concept
            for rule in self.similarity_group_tag_rules.values()
            for concept in (
                rule.required_concepts
                + rule.supporting_concepts
                + rule.forbidden_concepts
            )
            if concept not in self.similarity_semantic_concepts
        }
        if unknown_concepts:
            raise ValueError(
                "素材组规则引用了未定义的语义概念："
                + "、".join(sorted(unknown_concepts))
            )


class SimilarityGroupTagRule(BaseModel):
    dimension: str = Field(min_length=1, max_length=80)
    fields: list[str] = Field(min_length=1, max_length=10)
    keywords: list[str] = Field(default_factory=list, max_length=40)
    minimum_keyword_hits: int = Field(default=1, ge=1, le=10)
    required_concepts: list[str] = Field(default_factory=list, max_length=10)
    supporting_concepts: list[str] = Field(default_factory=list, max_length=10)
    forbidden_concepts: list[str] = Field(default_factory=list, max_length=10)
    hard_gate: bool | None = None

    def model_post_init(self, __context: object, /) -> None:
        if not self.keywords and not self.required_concepts:
            raise ValueError("素材组标签规则至少需要关键词或必需语义概念")


class ProfileLoader:
    def __init__(self, settings: Settings) -> None:
        self.directory = Path(settings.profiles_directory)

    def get_filter_profile(self, profile_id: str) -> FilterProfile:
        return FilterProfile.model_validate(self._load("filters", profile_id))

    def get_beautify_profile(self, profile_id: str) -> BeautifyProfile:
        return BeautifyProfile.model_validate(self._load("beautify", profile_id))

    def get_similarity_profile(self, profile_id: str) -> SimilarityProfile:
        try:
            return SimilarityProfile.model_validate(self._load("tags", profile_id))
        except ValidationError as exc:
            raise ProfileNotFoundError(f"Profile 不是有效的相似匹配配置: {profile_id}") from exc

    def list_filter_profiles(self) -> list[FilterProfile]:
        return [FilterProfile.model_validate(payload) for payload in self._list("filters")]

    def list_beautify_profiles(self) -> list[BeautifyProfile]:
        return [BeautifyProfile.model_validate(payload) for payload in self._list("beautify")]

    def list_similarity_profiles(self) -> list[SimilarityProfile]:
        profiles: list[SimilarityProfile] = []
        for payload in self._list("tags"):
            try:
                profiles.append(SimilarityProfile.model_validate(payload))
            except ValidationError:
                continue
        return profiles

    def _load(self, profile_type: str, profile_id: str) -> dict[str, object]:
        path = self.directory / profile_type / f"{profile_id}.yaml"
        if not path.is_file():
            raise ProfileNotFoundError(f"Profile 不存在: {profile_id}")
        with path.open("r", encoding="utf-8") as file:
            payload = yaml.safe_load(file)
        if not isinstance(payload, dict) or payload.get("id") != profile_id:
            raise ProfileNotFoundError(f"Profile 配置无效: {profile_id}")
        return payload

    def _list(self, profile_type: str) -> list[dict[str, object]]:
        directory = self.directory / profile_type
        if not directory.is_dir():
            return []
        profiles: list[dict[str, object]] = []
        for path in sorted(directory.glob("*.yaml")):
            with path.open("r", encoding="utf-8") as file:
                payload = yaml.safe_load(file)
            if isinstance(payload, dict):
                profiles.append(payload)
        return profiles
