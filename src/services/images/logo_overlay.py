from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from src.services.images.mosaic import Box, expand_box


@lru_cache(maxsize=8)
def load_overlay_asset(asset_id: str) -> tuple[np.ndarray, str]:
    if asset_id != "xiaodang_v1":
        raise ValueError(f"unsupported logo overlay asset: {asset_id}")
    path = Path("assets/redaction/xiaodang_v1.png")
    data = path.read_bytes()
    decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if decoded is None:
        raise ValueError("unable to decode Xiaodang overlay asset")

    # The supplied brand artwork contains the mascot, its caption and a white
    # background. Derive a smooth alpha channel from that matte so the exact
    # approved artwork can be centered on the generated sticker plate. A future
    # native RGBA asset works without conversion.
    if decoded.ndim != 3:
        raise ValueError("overlay asset must be a color image")
    if decoded.shape[2] == 4:
        rgba = decoded
    else:
        bgr = decoded[:, :, :3]
        distance_from_white = 255 - np.min(bgr, axis=2)
        alpha = np.clip((distance_from_white.astype(np.float32) - 3) * 3.2, 0, 255).astype(
            np.uint8
        )
        rgba = np.dstack([bgr, alpha])
    active = rgba[:, :, 3] > 8
    if not np.any(active):
        raise ValueError("overlay asset has no visible pixels")
    ys, xs = np.where(active)
    rgba = rgba[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    return rgba, hashlib.sha256(data).hexdigest()


def apply_logo_overlays(
    image_bgr: np.ndarray,
    boxes: list[Box],
    *,
    asset_id: str,
    expansion: float,
    scale: float,
) -> tuple[np.ndarray, list[Box], str]:
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("image must not be empty")
    overlay, asset_sha256 = load_overlay_asset(asset_id)
    result = image_bgr.copy()
    image_height, image_width = result.shape[:2]
    applied: list[Box] = []

    for raw_box in boxes:
        target = expand_box(
            raw_box,
            image_width=image_width,
            image_height=image_height,
            expansion=expansion,
        )
        if target is None:
            continue
        x0, y0, x1, y1 = target
        target_width, target_height = x1 - x0, y1 - y0
        asset_height, asset_width = overlay.shape[:2]
        render_width = max(1, round(target_width * scale))
        render_height = max(1, round(target_height * scale))
        # A wordmark is often much wider than the mascot. Scaling one mascot to
        # cover that width would hide a large part of the photo. Keep the sticker
        # close to the detected logo's proportions, use a rounded opaque plate
        # to hide every source pixel, and center one undistorted mascot on it.
        rendered = np.zeros((render_height, render_width, 4), dtype=np.uint8)
        rendered[:, :, :3] = 255
        radius = max(1, round(min(render_width, render_height) * 0.10))
        plate_alpha = np.zeros((render_height, render_width), dtype=np.uint8)
        cv2.rectangle(
            plate_alpha,
            (radius, 0),
            (max(radius, render_width - radius - 1), render_height - 1),
            255,
            -1,
        )
        cv2.rectangle(
            plate_alpha,
            (0, radius),
            (render_width - 1, max(radius, render_height - radius - 1)),
            255,
            -1,
        )
        for corner_x in (radius, render_width - radius - 1):
            for corner_y in (radius, render_height - radius - 1):
                cv2.circle(plate_alpha, (corner_x, corner_y), radius, 255, -1)
        rendered[:, :, 3] = plate_alpha

        unit_height = max(1, round(render_height * 0.84))
        unit_width = max(1, round(asset_width * unit_height / asset_height))
        if unit_width > round(render_width * 0.90):
            unit_width = max(1, round(render_width * 0.90))
            unit_height = max(1, round(asset_height * unit_width / asset_width))
        unit = cv2.resize(
            overlay,
            (unit_width, unit_height),
            interpolation=cv2.INTER_AREA if unit_height < asset_height else cv2.INTER_CUBIC,
        )
        start_x = (render_width - unit_width) // 2
        start_y = (render_height - unit_height) // 2
        alpha = unit[:, :, 3:4].astype(np.float32) / 255.0
        destination = rendered[
            start_y : start_y + unit_height, start_x : start_x + unit_width, :3
        ].astype(np.float32)
        rendered[
            start_y : start_y + unit_height, start_x : start_x + unit_width, :3
        ] = np.clip(
            unit[:, :, :3].astype(np.float32) * alpha + destination * (1.0 - alpha),
            0,
            255,
        ).astype(np.uint8)
        center_x, center_y = (x0 + x1) // 2, (y0 + y1) // 2
        left, top = center_x - render_width // 2, center_y - render_height // 2
        right, bottom = left + render_width, top + render_height
        clip_x0, clip_y0 = max(0, left), max(0, top)
        clip_x1, clip_y1 = min(image_width, right), min(image_height, bottom)
        if clip_x1 <= clip_x0 or clip_y1 <= clip_y0:
            continue
        source = rendered[
            clip_y0 - top : clip_y1 - top,
            clip_x0 - left : clip_x1 - left,
        ]
        alpha = source[:, :, 3:4].astype(np.float32) / 255.0
        destination = result[clip_y0:clip_y1, clip_x0:clip_x1].astype(np.float32)
        blended = source[:, :, :3].astype(np.float32) * alpha + destination * (1.0 - alpha)
        result[clip_y0:clip_y1, clip_x0:clip_x1] = np.clip(blended, 0, 255).astype(
            np.uint8
        )
        applied.append((clip_x0, clip_y0, clip_x1, clip_y1))
    return result, applied, asset_sha256
