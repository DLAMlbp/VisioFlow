from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from src.services.profiles import BeautifyProfile


@dataclass(frozen=True)
class PreviewCorrection:
    profile: BeautifyProfile
    reasons: tuple[str, ...]


def make_preview(image_bytes: bytes, long_side: int = 768) -> bytes:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            preview = ImageOps.exif_transpose(image).convert("RGB")
            preview.thumbnail((long_side, long_side), Image.Resampling.LANCZOS)
            output = BytesIO()
            preview.save(output, format="JPEG", quality=90, optimize=True)
            return output.getvalue()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("图片无法解码，不能执行美化预演") from exc


def evaluate_acceptance(
    before_bytes: bytes,
    after_bytes: bytes,
) -> list[dict[str, object]]:
    before = _measure(before_bytes)
    after = _measure(after_bytes)
    exposure_passed = (
        after["highlight_ratio"] <= max(0.04, before["highlight_ratio"] + 0.02)
        and after["shadow_ratio"] <= max(0.07, before["shadow_ratio"] + 0.03)
        and 30 <= after["brightness"] <= 225
    )
    color_passed = after["saturation"] <= max(185, before["saturation"] * 1.4 + 10) and after[
        "channel_cast"
    ] <= max(38, before["channel_cast"] + 10)
    noise_passed = after["noise"] <= max(before["noise"] * 1.25, before["noise"] + 2)
    sharpening_passed = after["edge_variance"] <= max(
        before["edge_variance"] * 2.4,
        before["edge_variance"] + 80,
    )
    return [
        _check(
            "exposure",
            exposure_passed,
            before,
            after,
            ("brightness", "shadow_ratio", "highlight_ratio"),
        ),
        _check("color", color_passed, before, after, ("saturation", "channel_cast")),
        _check("noise", noise_passed, before, after, ("noise",)),
        _check("sharpening", sharpening_passed, before, after, ("edge_variance",)),
    ]


def correct_after_preview(
    profile: BeautifyProfile,
    checks: list[dict[str, object]],
) -> PreviewCorrection:
    failed = {str(check["name"]) for check in checks if not bool(check["passed"])}
    updates: dict[str, object] = {}
    reasons: list[str] = []
    if "exposure" in failed:
        updates.update(
            brightness=min(profile.brightness, 1.03),
            highlight_recovery=max(profile.highlight_recovery, 0.22),
        )
        reasons.append("小图预演发现曝光风险，已降低亮度并加强高光保护")
    if "color" in failed:
        updates.update(
            color=min(profile.color, 1.05),
            white_balance_strength=min(profile.white_balance_strength, 0.2),
        )
        reasons.append("小图预演发现色彩风险，已限制饱和度和白平衡强度")
    if "noise" in failed:
        updates.update(
            denoise_strength=max(profile.denoise_strength, 0.12),
            local_clarity_strength=min(profile.local_clarity_strength, 0.12),
        )
        reasons.append("小图预演发现噪声风险，已加强轻度降噪并限制局部清晰度")
    if "sharpening" in failed:
        updates.update(
            sharpness=min(profile.sharpness, 1.05),
            local_clarity_strength=min(
                float(updates.get("local_clarity_strength", profile.local_clarity_strength)),
                0.1,
            ),
        )
        reasons.append("小图预演发现锐化风险，已限制锐化和局部清晰度")
    return PreviewCorrection(profile.model_copy(update=updates), tuple(reasons))


def acceptance_passed(checks: list[dict[str, object]]) -> bool:
    return all(bool(check.get("passed")) for check in checks)


def _measure(image_bytes: bytes) -> dict[str, float]:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            rgb = np.asarray(ImageOps.exif_transpose(image).convert("RGB"))
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("图片无法解码，不能执行美化验收") from exc
    if max(rgb.shape[:2]) > 1024:
        scale = 1024 / max(rgb.shape[:2])
        rgb = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=1)
    channel_means = np.mean(rgb, axis=(0, 1))
    return {
        "brightness": round(float(np.mean(gray)), 4),
        "shadow_ratio": round(float(np.mean(gray <= 5)), 6),
        "highlight_ratio": round(float(np.mean(gray >= 250)), 6),
        "saturation": round(float(np.mean(hsv[:, :, 1])), 4),
        "channel_cast": round(float(np.max(channel_means) - np.min(channel_means)), 4),
        "noise": round(float(np.mean(np.abs(gray.astype(np.float32) - blurred))), 4),
        "edge_variance": round(float(cv2.Laplacian(gray, cv2.CV_32F).var()), 4),
    }


def _check(
    name: str,
    passed: bool,
    before: dict[str, float],
    after: dict[str, float],
    fields: tuple[str, ...],
) -> dict[str, object]:
    return {
        "name": name,
        "passed": passed,
        "before": {field: before[field] for field in fields},
        "after": {field: after[field] for field in fields},
        "reason": "验收通过" if passed else "变化超过安全范围",
    }
