from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from threading import Lock

import cv2
import numpy as np


@lru_cache(maxsize=2)
def _network(model_path: str) -> tuple[object, Lock]:
    path = Path(model_path)
    if not path.is_file():
        raise RuntimeError(f"LaMa model is missing: {path}")
    try:
        network = cv2.dnn.readNetFromONNX(str(path))
    except cv2.error as exc:
        raise RuntimeError(f"unable to load LaMa model: {exc}") from None
    return network, Lock()


def erase_lama_opencv(
    image_bgr: np.ndarray,
    mask: np.ndarray,
    *,
    model_path: str | Path,
) -> np.ndarray:
    """Run OpenCV's Apache-2.0 LaMa model on a square, aspect-safe crop.

    Pre/post-processing follows the OpenCV model demo. Only masked pixels are
    pasted back, which is the hard guarantee that unrelated content is kept.
    """
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return image_bgr.copy()

    image_height, image_width = image_bgr.shape[:2]
    mark_width = int(xs.max() - xs.min() + 1)
    mark_height = int(ys.max() - ys.min() + 1)
    context = max(32, round(max(mark_width, mark_height) * 0.25))
    x0 = max(0, int(xs.min()) - context)
    y0 = max(0, int(ys.min()) - context)
    x1 = min(image_width, int(xs.max()) + 1 + context)
    y1 = min(image_height, int(ys.max()) + 1 + context)
    crop = image_bgr[y0:y1, x0:x1]
    crop_mask = mask[y0:y1, x0:x1]
    crop_height, crop_width = crop.shape[:2]

    square_size = max(crop_height, crop_width)
    top = (square_size - crop_height) // 2
    bottom = square_size - crop_height - top
    left = (square_size - crop_width) // 2
    right = square_size - crop_width - left
    square = cv2.copyMakeBorder(
        crop, top, bottom, left, right, cv2.BORDER_REFLECT_101
    )
    square_mask = cv2.copyMakeBorder(
        crop_mask,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_CONSTANT,
        value=0,
    )

    image_blob = cv2.dnn.blobFromImage(
        square, 1.0 / 255.0, (512, 512), (0, 0, 0), False, False
    )
    mask_blob = cv2.dnn.blobFromImage(
        square_mask, 1.0, (512, 512), (0,), False, False
    )
    network, lock = _network(str(model_path))
    try:
        with lock:
            network.setInput(image_blob, "image")
            network.setInput((mask_blob > 0).astype(np.float32), "mask")
            output = network.forward()
    except cv2.error as exc:
        raise RuntimeError(f"LaMa inference failed: {exc}") from None

    output_square = np.asarray(output)[0].transpose(1, 2, 0).astype(np.uint8)
    output_square = cv2.resize(
        output_square, (square_size, square_size), interpolation=cv2.INTER_LINEAR
    )
    output_crop = output_square[top : top + crop_height, left : left + crop_width]
    result = image_bgr.copy()
    region = result[y0:y1, x0:x1]
    hole = crop_mask > 127
    region[hole] = output_crop[hole]
    result[y0:y1, x0:x1] = region
    return result
