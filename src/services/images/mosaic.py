from __future__ import annotations

from collections.abc import Iterable

import cv2
import numpy as np

Box = tuple[int, int, int, int]


def expand_box(
    box: Box,
    *,
    image_width: int,
    image_height: int,
    expansion: float,
) -> Box | None:
    """Expand an xyxy box and clamp it to the image."""
    x0, y0, x1, y1 = box
    x0, x1 = sorted((int(x0), int(x1)))
    y0, y1 = sorted((int(y0), int(y1)))
    if x1 <= 0 or y1 <= 0 or x0 >= image_width or y0 >= image_height:
        return None
    width = max(0, x1 - x0)
    height = max(0, y1 - y0)
    if width == 0 or height == 0:
        return None
    pad_x = round(width * expansion)
    pad_y = round(height * expansion)
    expanded = (
        max(0, x0 - pad_x),
        max(0, y0 - pad_y),
        min(image_width, x1 + pad_x),
        min(image_height, y1 + pad_y),
    )
    return expanded if expanded[2] > expanded[0] and expanded[3] > expanded[1] else None


def apply_pixel_mosaic(
    image_bgr: np.ndarray,
    boxes: Iterable[Box],
    *,
    expansion: float = 0.12,
    block_ratio: float = 0.08,
) -> tuple[np.ndarray, list[Box]]:
    """Pixelate selected boxes and return the exact clamped boxes that changed."""
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("image must not be empty")
    if image_bgr.ndim not in {2, 3}:
        raise ValueError("image must be a grayscale, BGR, or BGRA array")

    result = image_bgr.copy()
    height, width = result.shape[:2]
    applied: list[Box] = []
    for box in boxes:
        target = expand_box(
            box,
            image_width=width,
            image_height=height,
            expansion=expansion,
        )
        if target is None:
            continue
        x0, y0, x1, y1 = target
        region = result[y0:y1, x0:x1]
        region_height, region_width = region.shape[:2]
        block = max(4, round(min(region_width, region_height) * block_ratio))
        small_width = max(1, region_width // block)
        small_height = max(1, region_height // block)
        small = cv2.resize(region, (small_width, small_height), interpolation=cv2.INTER_AREA)
        pixelated = cv2.resize(
            small,
            (region_width, region_height),
            interpolation=cv2.INTER_NEAREST,
        )
        result[y0:y1, x0:x1] = pixelated
        applied.append(target)
    return result, applied
