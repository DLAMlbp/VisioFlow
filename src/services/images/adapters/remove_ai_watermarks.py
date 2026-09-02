from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

FillBackend = Literal["cv2", "migan"]


def erase_mask(
    image_bgr: np.ndarray,
    mask: np.ndarray,
    *,
    backend: FillBackend = "cv2",
    radius: int = 3,
) -> np.ndarray:
    """Use remove-ai-watermarks' region eraser when installed.

    The production redaction extra pins the Apache-2.0 upstream package. The
    default development install retains an OpenCV fallback with the same mask
    polarity, so configuration and unit tests do not pull its unrelated C2PA
    dependencies.
    """
    if image_bgr.shape[:2] != mask.shape[:2]:
        raise ValueError("mask shape must match image shape")
    if mask.dtype != np.uint8:
        mask = np.where(mask > 0, 255, 0).astype(np.uint8)
    if not np.any(mask):
        return image_bgr.copy()
    try:
        from remove_ai_watermarks.region_eraser import erase
    except ImportError:
        if backend != "cv2":
            raise RuntimeError(
                "MI-GAN requires the pinned remove-ai-watermarks redaction extra"
            ) from None
        return cv2.inpaint(image_bgr, mask, radius, cv2.INPAINT_TELEA)
    return erase(
        image_bgr,
        mask=mask,
        backend=backend,
        dilate=0,
        cv2_method="telea",
        cv2_radius=radius,
    )

