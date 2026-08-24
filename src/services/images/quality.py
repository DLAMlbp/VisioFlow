from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from math import prod

from PIL import Image, ImageChops, ImageFilter, ImageStat, UnidentifiedImageError

from src.core.config import Settings


@dataclass(frozen=True)
class ImageQualityMetrics:
    sharpness_score: float
    exposure_score: float
    contrast_score: float
    noise_score: float
    raw_metrics: dict[str, float]

    def as_db_values(self) -> dict[str, object]:
        return {
            "sharpness_score": self.sharpness_score,
            "exposure_score": self.exposure_score,
            "contrast_score": self.contrast_score,
            "noise_score": self.noise_score,
            "raw_metrics_json": self.raw_metrics,
        }


class QualityEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(self, thumbnail_bytes: bytes) -> ImageQualityMetrics:
        try:
            with Image.open(BytesIO(thumbnail_bytes)) as image:
                image.load()
                grayscale = image.convert("L")
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError("缩略图无法解码") from exc

        histogram = grayscale.histogram()
        pixel_count = grayscale.width * grayscale.height
        brightness_mean = ImageStat.Stat(grayscale).mean[0]
        contrast_stddev = ImageStat.Stat(grayscale).stddev[0]
        shadow_ratio = sum(histogram[:6]) / pixel_count
        highlight_ratio = sum(histogram[250:]) / pixel_count
        edge_variance = self._edge_variance(grayscale)
        noise_residual = self._noise_residual(grayscale)

        return ImageQualityMetrics(
            sharpness_score=round(self._sharpness_score(edge_variance), 2),
            exposure_score=round(
                self._exposure_score(brightness_mean, shadow_ratio, highlight_ratio), 2
            ),
            contrast_score=round(self._contrast_score(contrast_stddev), 2),
            noise_score=round(self._noise_score(noise_residual), 2),
            raw_metrics={
                "brightness_mean": round(brightness_mean, 4),
                "shadow_ratio": round(shadow_ratio, 6),
                "highlight_ratio": round(highlight_ratio, 6),
                "contrast_stddev": round(contrast_stddev, 4),
                "edge_variance": round(edge_variance, 4),
                "noise_residual": round(noise_residual, 4),
            },
        )

    @staticmethod
    def calculate_weighted_quality_score(
        *,
        sharpness: float,
        exposure: float,
        contrast: float,
        noise: float,
    ) -> float:
        weighted_components = (
            (sharpness / 100, 0.38),
            (exposure / 100, 0.34),
            (contrast / 100, 0.13),
            (noise / 100, 0.15),
        )
        score = 100 * prod(value**weight for value, weight in weighted_components)
        return round(QualityEngine._clamp_score(score), 2)

    def _sharpness_score(self, edge_variance: float) -> float:
        return self._clamp_score(edge_variance / self.settings.quality_sharpness_reference * 100)

    def _exposure_score(
        self,
        brightness_mean: float,
        shadow_ratio: float,
        highlight_ratio: float,
    ) -> float:
        brightness_penalty = (
            abs(brightness_mean - 128)
            / 128
            * self.settings.quality_exposure_brightness_penalty
        )
        clipping_penalty = max(shadow_ratio, highlight_ratio) * self.settings.quality_exposure_clipping_penalty
        return self._clamp_score(100 - brightness_penalty - clipping_penalty)

    def _contrast_score(self, contrast_stddev: float) -> float:
        lower = self.settings.quality_contrast_min_stddev
        upper = self.settings.quality_contrast_max_stddev
        return self._clamp_score((contrast_stddev - lower) / (upper - lower) * 100)

    def _noise_score(self, noise_residual: float) -> float:
        return self._clamp_score(100 - noise_residual * self.settings.quality_noise_penalty)

    @staticmethod
    def _edge_variance(grayscale: Image.Image) -> float:
        edges = grayscale.filter(ImageFilter.FIND_EDGES)
        if edges.width > 2 and edges.height > 2:
            edges = edges.crop((1, 1, edges.width - 1, edges.height - 1))
        return ImageStat.Stat(edges).var[0]

    @staticmethod
    def _noise_residual(grayscale: Image.Image) -> float:
        blurred = grayscale.filter(ImageFilter.GaussianBlur(radius=1))
        residual = ImageChops.difference(grayscale, blurred)
        return ImageStat.Stat(residual).mean[0]

    @staticmethod
    def _clamp_score(value: float) -> float:
        return max(0.0, min(100.0, value))
