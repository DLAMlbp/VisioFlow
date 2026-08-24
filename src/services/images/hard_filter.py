from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError

from src.core.config import Settings
from src.services.profiles import HardRulesProfile


class RejectCode:
    IMAGE_TOO_SMALL = "IMAGE_TOO_SMALL"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    EXTREME_BLUR = "EXTREME_BLUR"
    EXTREME_OVEREXPOSURE = "EXTREME_OVEREXPOSURE"
    EXTREME_UNDEREXPOSURE = "EXTREME_UNDEREXPOSURE"
    LOCAL_HEAVY_SHADOW = "LOCAL_HEAVY_SHADOW"
    QUALITY_SCORE_TOO_LOW = "QUALITY_SCORE_TOO_LOW"
    SHARPNESS_SCORE_TOO_LOW = "SHARPNESS_SCORE_TOO_LOW"
    EXPOSURE_SCORE_TOO_LOW = "EXPOSURE_SCORE_TOO_LOW"
    CONTRAST_SCORE_TOO_LOW = "CONTRAST_SCORE_TOO_LOW"
    NOISE_SCORE_TOO_LOW = "NOISE_SCORE_TOO_LOW"
    SOLID_COLOR = "SOLID_COLOR"
    DUPLICATE_IMAGE = "DUPLICATE_IMAGE"


@dataclass(frozen=True)
class HardFilterResult:
    reject_codes: tuple[str, ...]
    warning_codes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.reject_codes


class HardFilterService:
    def __init__(self, settings: Settings, rules: HardRulesProfile | None = None) -> None:
        self.settings = settings
        self.rules = rules

    def evaluate(self, thumbnail_bytes: bytes, *, width: int, height: int) -> HardFilterResult:
        size_rejection = self._validate_size(width, height)
        if size_rejection is not None:
            return HardFilterResult((size_rejection,))

        try:
            with Image.open(BytesIO(thumbnail_bytes)) as image:
                image.load()
                grayscale = image.convert("L")
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError("缩略图无法解码") from exc

        reject_codes: list[str] = []
        warning_codes: list[str] = []
        histogram = grayscale.histogram()
        pixel_count = grayscale.width * grayscale.height
        underexposed_ratio = sum(histogram[:6]) / pixel_count
        overexposed_ratio = sum(histogram[250:]) / pixel_count
        visible_content_ratio = sum(histogram[32:]) / pixel_count
        is_overexposed = overexposed_ratio >= self._rule("max_overexposed_ratio")
        is_underexposed = underexposed_ratio >= self._rule("max_underexposed_ratio")
        is_near_black_frame = (
            underexposed_ratio >= self._rule("reject_underexposed_ratio")
            and visible_content_ratio <= self._rule("min_visible_content_ratio")
        )
        is_solid_color = ImageStat.Stat(grayscale).stddev[0] <= self._rule("max_solid_color_stddev")

        if is_overexposed:
            warning_codes.append(RejectCode.EXTREME_OVEREXPOSURE)
        if is_underexposed:
            warning_codes.append(RejectCode.EXTREME_UNDEREXPOSURE)
        if is_solid_color:
            reject_codes.append(RejectCode.SOLID_COLOR)

        # A nearly black frame with only a tiny bright area is not usable on-site evidence.
        # A dim scene containing visible material/detail remains a warning, not a rejection.
        if is_near_black_frame:
            reject_codes.append(RejectCode.EXTREME_UNDEREXPOSURE)

        # A blank or clipped frame also has little edge detail, but it is not evidence of
        # lens/camera blur. It is rejected by the solid/near-black rules above instead.
        is_low_information_frame = is_solid_color or is_overexposed or is_underexposed
        if (
            not is_low_information_frame
            and self._edge_variance(grayscale) < self._rule("min_edge_variance")
        ):
            reject_codes.append(RejectCode.EXTREME_BLUR)

        # A truly blurred scene cannot serve as usable renovation evidence and must exit
        # before beautification and AI tagging. Exposure warnings remain non-blocking.
        return HardFilterResult(tuple(reject_codes), tuple(warning_codes))

    def _validate_size(self, width: int, height: int) -> str | None:
        min_short_side = min(self._rule("min_width"), self._rule("min_height"))
        max_long_side = max(self._rule("max_width"), self._rule("max_height"))
        if min(width, height) < min_short_side:
            return RejectCode.IMAGE_TOO_SMALL
        if max(width, height) > max_long_side:
            return RejectCode.IMAGE_TOO_LARGE
        return None

    def _rule(self, name: str) -> float | int:
        if self.rules is not None:
            return getattr(self.rules, name)
        fallback_names = {
            "min_width": "hard_filter_min_width",
            "min_height": "hard_filter_min_height",
            "max_width": "hard_filter_max_width",
            "max_height": "hard_filter_max_height",
            "min_edge_variance": "hard_filter_min_edge_variance",
            "max_overexposed_ratio": "hard_filter_overexposed_ratio",
            "max_underexposed_ratio": "hard_filter_underexposed_ratio",
            "reject_underexposed_ratio": "hard_filter_reject_underexposed_ratio",
            "min_visible_content_ratio": "hard_filter_min_visible_content_ratio",
            "max_solid_color_stddev": "hard_filter_solid_color_stddev",
        }
        return getattr(self.settings, fallback_names[name])

    @staticmethod
    def _edge_variance(grayscale: Image.Image) -> float:
        edges = grayscale.filter(ImageFilter.FIND_EDGES)
        # FIND_EDGES treats the image boundary as a strong edge; exclude it from the metric.
        if edges.width > 2 and edges.height > 2:
            edges = edges.crop((1, 1, edges.width - 1, edges.height - 1))
        return ImageStat.Stat(edges).var[0]
