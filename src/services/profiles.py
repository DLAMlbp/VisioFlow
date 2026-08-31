from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

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


class ProcessingStandard(BaseModel):
    """One conditional filter standard evaluated independently for every image."""

    id: str
    name: str = ""
    version: int = Field(ge=1)
    description: str
    activation_rule: str = Field(min_length=3, max_length=2000)
    filter_rule: str = Field(min_length=3, max_length=2000)
    priority: int = Field(default=100, ge=0, le=10000)


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
    similarity_auto_threshold: float = Field(ge=0, le=1)
    similarity_review_threshold: float = Field(ge=0, le=1)
    similarity_min_margin: float = Field(ge=0, le=1)


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
