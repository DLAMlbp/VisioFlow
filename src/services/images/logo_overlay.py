from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from src.services.images.mosaic import Box, expand_box


@lru_cache(maxsize=8)
def load_overlay_asset(asset_id: str) -> tuple[np.ndarray, str]:
    if asset_id not in {"xiaodang_v1", "xiaodang_cutout_v1"}:
        raise ValueError(f"unsupported logo overlay asset: {asset_id}")
    # Keep the legacy identifier compatible, but always render the approved
    # transparent cutout. This upgrades already-snapshotted jobs without an
    # opaque white plate.
    path = Path("assets/redaction/xiaodang_cutout_v1.png")
    data = path.read_bytes()
    decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if decoded is None:
        raise ValueError("unable to decode Xiaodang overlay asset")

    # The checked-in delivery asset is RGBA. The RGB fallback remains for
    # operator-supplied replacements that still use a white matte.
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
    asset_anchor_x_ratio: float = 0.34,
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
        # The requested visual language is a transparent mascot placed over one
        # identifying word, not an opaque card covering the whole wordmark.
        # Height follows the detected text. Very wide OCR lines receive a modest
        # width-derived boost so the mascot still obscures a meaningful token.
        render_height = max(
            1,
            round(target_height * scale),
            round(target_width * 1.05 * asset_height / asset_width),
        )
        render_width = max(1, round(asset_width * render_height / asset_height))
        rendered = cv2.resize(
            overlay,
            (render_width, render_height),
            interpolation=(
                cv2.INTER_AREA if render_height < asset_height else cv2.INTER_CUBIC
            ),
        )
        center_x = (x0 + x1) // 2
        center_y = (y0 + y1) // 2
        # The mascot occupies the left side of the transparent artwork and the
        # caption sits on its right. Anchor the mascot (not the full canvas) on
        # the APP token, matching the approved reference composition.
        left = max(x0, center_x - round(render_width * asset_anchor_x_ratio))
        top = center_y - render_height // 2
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
