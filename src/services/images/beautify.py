from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from math import atan2, ceil, degrees, hypot, radians, sin
from statistics import pstdev

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageOps, ImageStat, UnidentifiedImageError

from src.services.profiles import BeautifyProfile


@dataclass(frozen=True)
class BeautifyResult:
    image_bytes: bytes
    straighten_applied: bool
    local_tone_applied: bool
    glare_reduction_applied: bool
    local_clarity_applied: bool


@dataclass(frozen=True)
class OrientationNormalizationResult:
    image_bytes: bytes
    exif_orientation_applied: bool
    straighten_applied: bool


class NaturalBeautifyService:
    def enhance(self, image_bytes: bytes, profile: BeautifyProfile) -> bytes:
        return self.enhance_with_details(image_bytes, profile).image_bytes

    def enhance_with_details(
        self,
        image_bytes: bytes,
        profile: BeautifyProfile,
    ) -> BeautifyResult:
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                image.load()
                enhanced = ImageOps.exif_transpose(image).convert("RGB")
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError("原图无法解码，无法美化") from exc

        enhanced, straighten_applied = self._auto_straighten(enhanced, profile)
        enhanced = self._apply_white_balance(enhanced, profile)
        enhanced = self._recover_tonal_detail(enhanced, profile)
        enhanced, local_tone_applied = self._enhance_local_tone(enhanced, profile)
        enhanced = self._reduce_noise(enhanced, profile)
        enhanced, glare_reduction_applied = self._reduce_glare(enhanced, profile)
        enhanced = ImageEnhance.Brightness(enhanced).enhance(profile.brightness)
        enhanced = ImageEnhance.Contrast(enhanced).enhance(profile.contrast)
        enhanced = ImageEnhance.Color(enhanced).enhance(profile.color)
        enhanced, local_clarity_applied = self._enhance_local_clarity(enhanced, profile)

        enhanced = self._ensure_minimum_output_size(enhanced, profile)
        output = BytesIO()
        enhanced.save(output, format="JPEG", quality=profile.jpeg_quality, optimize=True)
        return BeautifyResult(
            image_bytes=output.getvalue(),
            straighten_applied=straighten_applied,
            local_tone_applied=local_tone_applied,
            glare_reduction_applied=glare_reduction_applied,
            local_clarity_applied=local_clarity_applied,
        )

    def normalize_orientation(
        self,
        image_bytes: bytes,
        profile: BeautifyProfile,
    ) -> OrientationNormalizationResult:
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                image.load()
                exif_orientation_applied = image.getexif().get(274) not in {None, 1}
                normalized = ImageOps.exif_transpose(image).convert("RGB")
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError("原图无法解码，无法校正方向") from exc

        normalized, straighten_applied = self._auto_straighten(normalized, profile)
        output = BytesIO()
        normalized.save(output, format="JPEG", quality=profile.jpeg_quality, optimize=True)
        return OrientationNormalizationResult(
            image_bytes=output.getvalue(),
            exif_orientation_applied=exif_orientation_applied,
            straighten_applied=straighten_applied,
        )

    def prepare_delivery_image(self, image_bytes: bytes, profile: BeautifyProfile) -> bytes:
        """Encode an orientation-normalized image to the configured delivery size."""
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                image.load()
                delivery = self._ensure_minimum_output_size(image.convert("RGB"), profile)
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError("图片无法解码，无法生成交付图") from exc
        output = BytesIO()
        delivery.save(output, format="JPEG", quality=profile.jpeg_quality, optimize=True)
        return output.getvalue()

    @staticmethod
    def processing_reasons(profile: BeautifyProfile, result: BeautifyResult) -> list[str]:
        reasons = [
            "已自动校正轻微倾斜"
            if result.straighten_applied
            else "已保留原始构图与画面比例"
        ]
        if profile.auto_white_balance and profile.white_balance_strength > 0:
            reasons.append("已完成白平衡和色温微调")
        if profile.shadow_lift > 0 or profile.highlight_recovery > 0:
            reasons.append("已平衡高光和阴影细节")
        if result.local_tone_applied:
            reasons.append("已增强暗部和纹理区域的局部层次")
        if profile.denoise_strength > 0:
            reasons.append("已进行轻度降噪")
        if result.glare_reduction_applied:
            reasons.append("已压制局部反光和眩光")
        if result.local_clarity_applied:
            reasons.append("已增强主体与纹理边缘细节")
        return reasons

    @classmethod
    def _auto_straighten(cls, image: Image.Image, profile: BeautifyProfile) -> tuple[Image.Image, bool]:
        if not profile.auto_straighten:
            return image, False

        small_angle_limit = min(profile.max_straighten_degrees, 3)
        angle = cls._estimate_skew_angle(image, small_angle_limit)
        if angle is None and profile.max_straighten_degrees > small_angle_limit:
            angle = cls._estimate_skew_angle(image, profile.max_straighten_degrees)
        if angle is None:
            return image, False
        return cls._rotate_and_crop(image, angle), True

    @staticmethod
    def _rotate_and_crop(image: Image.Image, angle: float) -> Image.Image:
        rotated = image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        inset = ceil(max(image.size) * sin(radians(abs(angle)))) + 2
        cropped = rotated.crop(
            (inset, inset, rotated.width - inset, rotated.height - inset)
        )
        return cropped.resize(image.size, Image.Resampling.LANCZOS)

    @staticmethod
    def _ensure_minimum_output_size(image: Image.Image, profile: BeautifyProfile) -> Image.Image:
        """Keep native detail when available and upscale smaller delivery images to 2K."""
        long_side = max(image.size)
        if long_side >= profile.min_output_long_side:
            return image
        scale = profile.min_output_long_side / long_side
        target_size = (
            max(1, round(image.width * scale)),
            max(1, round(image.height * scale)),
        )
        return image.resize(target_size, Image.Resampling.LANCZOS)

    @staticmethod
    def _estimate_skew_angle(image: Image.Image, max_degrees: float) -> float | None:
        preview = image.convert("L")
        preview.thumbnail((320, 320), Image.Resampling.BILINEAR)
        edges = cv2.Canny(np.asarray(preview), 60, 180, apertureSize=3)
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 360,
            threshold=30,
            minLineLength=max(preview.width, preview.height) * 0.22,
            maxLineGap=8,
        )
        if lines is None:
            return None

        candidates: list[tuple[float, float]] = []
        for line in lines.reshape(-1, 4):
            x1, y1, x2, y2 = (int(value) for value in line)
            line_length = hypot(x2 - x1, y2 - y1)
            angle = ((degrees(atan2(y2 - y1, x2 - x1)) + 90) % 180) - 90
            reference = 0 if abs(angle) <= 45 else (-90 if angle < 0 else 90)
            correction = reference - angle
            if abs(correction) <= max_degrees:
                candidates.append((correction, line_length))

        if len(candidates) < 4:
            return None
        centers = [step / 2 for step in range(-int(max_degrees * 2), int(max_degrees * 2) + 1)]
        correction = max(
            centers,
            key=lambda center: sum(
                weight for value, weight in candidates if abs(value - center) <= 0.75
            ),
        )
        consistent_candidates = [
            candidate for candidate in candidates if abs(candidate[0] - correction) <= 0.75
        ]
        if len(consistent_candidates) < 4:
            return None
        consistent_weight = sum(weight for _, weight in consistent_candidates)
        if consistent_weight < sum(weight for _, weight in candidates) * 0.3:
            return None
        corrections = [candidate[0] for candidate in consistent_candidates]
        if pstdev(corrections) > 0.6:
            return None
        correction = NaturalBeautifyService._weighted_median(consistent_candidates)
        return -correction if abs(correction) >= 0.5 else None

    @staticmethod
    def _weighted_median(values: list[tuple[float, float]]) -> float:
        ordered = sorted(values)
        halfway = sum(weight for _, weight in ordered) / 2
        total = 0.0
        for value, weight in ordered:
            total += weight
            if total >= halfway:
                return value
        return ordered[-1][0]

    @staticmethod
    def _apply_white_balance(image: Image.Image, profile: BeautifyProfile) -> Image.Image:
        if not profile.auto_white_balance or profile.white_balance_strength == 0:
            return image

        means = ImageStat.Stat(image).mean
        target = sum(means) / len(means)
        channels = []
        for channel, mean in zip(image.split(), means, strict=True):
            gain = max(0.94, min(1.06, target / max(mean, 1)))
            corrected = channel.point(lambda value, factor=gain: min(255, round(value * factor)))
            channels.append(corrected)
        balanced = Image.merge("RGB", tuple(channels))
        return Image.blend(image, balanced, profile.white_balance_strength)

    @staticmethod
    def _recover_tonal_detail(image: Image.Image, profile: BeautifyProfile) -> Image.Image:
        if profile.shadow_lift == 0 and profile.highlight_recovery == 0:
            return image

        lookup = []
        for value in range(256):
            normalized = value / 255
            adjusted = value
            adjusted += profile.shadow_lift * 55 * (1 - normalized) ** 2
            adjusted -= profile.highlight_recovery * 55 * normalized**3
            lookup.append(max(0, min(255, round(adjusted))))
        return Image.merge("RGB", tuple(channel.point(lookup) for channel in image.split()))

    @staticmethod
    def _reduce_noise(image: Image.Image, profile: BeautifyProfile) -> Image.Image:
        if profile.denoise_strength == 0:
            return image
        rgb = np.asarray(image)
        denoised = cv2.bilateralFilter(rgb, d=5, sigmaColor=22, sigmaSpace=3)
        denoised_image = Image.fromarray(denoised)
        return Image.blend(image, denoised_image, profile.denoise_strength)

    @staticmethod
    def _enhance_local_tone(
        image: Image.Image,
        profile: BeautifyProfile,
    ) -> tuple[Image.Image, bool]:
        if profile.local_tone_strength == 0:
            return image, False

        rgb = np.asarray(image)
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
        lightness, green_red, blue_yellow = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=profile.local_tone_clip_limit, tileGridSize=(8, 8))
        local_lightness = clahe.apply(lightness)
        blended_lightness = cv2.addWeighted(
            lightness,
            1 - profile.local_tone_strength,
            local_lightness,
            profile.local_tone_strength,
            0,
        )
        enhanced = cv2.merge((blended_lightness, green_red, blue_yellow))
        return Image.fromarray(cv2.cvtColor(enhanced, cv2.COLOR_LAB2RGB)), True

    @staticmethod
    def _reduce_glare(image: Image.Image, profile: BeautifyProfile) -> tuple[Image.Image, bool]:
        if profile.glare_reduction_strength == 0:
            return image, False

        rgb = np.asarray(image).copy()
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        candidate_mask = np.where((hsv[:, :, 2] >= 240) & (hsv[:, :, 1] <= 75), 255, 0).astype(
            np.uint8
        )
        component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
            candidate_mask,
            connectivity=8,
        )
        glare_mask = np.zeros(candidate_mask.shape, dtype=np.uint8)
        minimum_area = max(30, int(candidate_mask.size * 0.00015))
        maximum_area = int(candidate_mask.size * 0.03)
        for label in range(1, component_count):
            x, y, width, height, area = stats[label]
            touches_edge = (
                x == 0
                or y == 0
                or x + width == candidate_mask.shape[1]
                or y + height == candidate_mask.shape[0]
            )
            if minimum_area <= area <= maximum_area and not touches_edge:
                glare_mask[labels == label] = 255
        if not np.any(glare_mask):
            return image, False

        alpha = cv2.GaussianBlur(glare_mask, (0, 0), sigmaX=5).astype(np.float32) / 255
        alpha *= profile.glare_reduction_strength
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        compressed_lightness = 215 + (lab[:, :, 0] - 215) * 0.45
        lab[:, :, 0] = lab[:, :, 0] * (1 - alpha) + compressed_lightness * alpha
        corrected = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)
        return Image.fromarray(corrected), True

    @staticmethod
    def _enhance_local_clarity(image: Image.Image, profile: BeautifyProfile) -> tuple[Image.Image, bool]:
        if profile.local_clarity_strength == 0:
            return image, False

        rgb = np.asarray(image).copy()
        grayscale = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        gradient_x = cv2.Sobel(grayscale, cv2.CV_32F, 1, 0, ksize=3)
        gradient_y = cv2.Sobel(grayscale, cv2.CV_32F, 0, 1, ksize=3)
        gradient = cv2.magnitude(gradient_x, gradient_y)
        edge_mask = np.clip((gradient - 28) / 90, 0, 1)
        edge_mask = cv2.GaussianBlur(edge_mask, (0, 0), sigmaX=1.2)
        blurred = cv2.GaussianBlur(rgb, (0, 0), sigmaX=1.1)
        detailed = cv2.addWeighted(rgb, 1.35, blurred, -0.35, 0)
        alpha = (edge_mask * profile.local_clarity_strength)[:, :, None]
        enhanced = rgb.astype(np.float32) * (1 - alpha) + detailed.astype(np.float32) * alpha
        return Image.fromarray(np.clip(enhanced, 0, 255).astype(np.uint8)), True
