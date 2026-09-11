from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

TARGET_ASPECT_WIDTH = 3
TARGET_ASPECT_HEIGHT = 4
NORMALIZATION_VERSION = 1


@dataclass(frozen=True)
class AspectNormalizationResult:
    image: Image.Image
    crop_box: tuple[int, int, int, int]
    input_size: tuple[int, int]
    output_size: tuple[int, int]
    applied: bool

    def as_audit(self, *, uploaded_size: tuple[int, int]) -> dict[str, object]:
        input_area = self.input_size[0] * self.input_size[1]
        output_area = self.output_size[0] * self.output_size[1]
        return {
            "version": NORMALIZATION_VERSION,
            "mode": "center_crop",
            "target_ratio": "3:4",
            "uploaded_size": list(uploaded_size),
            "input_size": list(self.input_size),
            "crop_box": list(self.crop_box),
            "processed_size": list(self.output_size),
            "retained_area_ratio": round(output_area / input_area, 4),
            "applied": self.applied,
        }


def normalize_to_portrait_3_4(image: Image.Image) -> AspectNormalizationResult:
    """Center-crop an oriented image to the largest exact 3:4 rectangle it contains."""

    width, height = image.size
    if width < TARGET_ASPECT_WIDTH or height < TARGET_ASPECT_HEIGHT:
        raise ValueError("图片尺寸过小，无法生成 3:4 竖图")

    unit = min(width // TARGET_ASPECT_WIDTH, height // TARGET_ASPECT_HEIGHT)
    target_width = unit * TARGET_ASPECT_WIDTH
    target_height = unit * TARGET_ASPECT_HEIGHT
    left = (width - target_width) // 2
    top = (height - target_height) // 2
    crop_box = (left, top, left + target_width, top + target_height)
    applied = (target_width, target_height) != (width, height)
    normalized = image.crop(crop_box) if applied else image.copy()
    return AspectNormalizationResult(
        image=normalized,
        crop_box=crop_box,
        input_size=(width, height),
        output_size=normalized.size,
        applied=applied,
    )


def is_portrait_3_4(width: int, height: int) -> bool:
    return width > 0 and height > 0 and width * TARGET_ASPECT_HEIGHT == height * TARGET_ASPECT_WIDTH
