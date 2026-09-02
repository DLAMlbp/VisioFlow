from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


@lru_cache(maxsize=2)
def _compiled_model(model_path: str):
    try:
        import openvino as ov
    except ImportError:
        raise RuntimeError("MI-GAN OpenVINO backend is not installed") from None
    path = Path(model_path)
    if not path.is_file():
        raise RuntimeError(f"MI-GAN model is missing: {path}")
    core = ov.Core()
    return core.compile_model(core.read_model(path), "CPU")


def erase_migan_openvino(
    image_bgr: np.ndarray,
    mask: np.ndarray,
    *,
    model_path: str | Path,
) -> np.ndarray:
    """MI-GAN inference adapted from remove-ai-watermarks' Apache-2.0 backend."""
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return image_bgr.copy()
    height, width = image_bgr.shape[:2]
    mark_width = int(xs.max() - xs.min() + 1)
    mark_height = int(ys.max() - ys.min() + 1)
    pad = max(16, round(max(mark_width, mark_height) * 0.4))
    x0, y0 = max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad)
    x1, y1 = min(width, int(xs.max()) + 1 + pad), min(height, int(ys.max()) + 1 + pad)
    crop = image_bgr[y0:y1, x0:x1]
    crop_mask = mask[y0:y1, x0:x1]
    crop_height, crop_width = crop.shape[:2]

    padded_height = ((crop_height + 7) // 8) * 8
    padded_width = ((crop_width + 7) // 8) * 8
    bottom, right = padded_height - crop_height, padded_width - crop_width
    padded_crop = cv2.copyMakeBorder(crop, 0, bottom, 0, right, cv2.BORDER_REFLECT_101)
    padded_mask = cv2.copyMakeBorder(
        crop_mask, 0, bottom, 0, right, cv2.BORDER_CONSTANT, value=0
    )

    rgb = cv2.cvtColor(padded_crop, cv2.COLOR_BGR2RGB)
    image_input = np.transpose(rgb, (2, 0, 1))[None].astype(np.uint8)
    known_input = ((padded_mask <= 127).astype(np.uint8) * 255)[None, None]
    compiled = _compiled_model(str(model_path))
    output = compiled({"image": image_input, "mask": known_input})
    output_tensor = np.asarray(next(iter(output.values())))[0]
    output_rgb = np.transpose(output_tensor, (1, 2, 0)).astype(np.uint8)
    output_bgr = cv2.cvtColor(output_rgb, cv2.COLOR_RGB2BGR)[:crop_height, :crop_width]

    result = image_bgr.copy()
    region = result[y0:y1, x0:x1]
    hole = crop_mask > 127
    region[hole] = output_bgr[hole]
    result[y0:y1, x0:x1] = region
    return result

