from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def extract_connected_white_background(image_bgr: np.ndarray) -> np.ndarray:
    """Remove only white pixels connected to the canvas border.

    Unlike a global white chroma key, this keeps the mascot's enclosed white
    eyes and body opaque. The source artwork and caption remain pixel-exact.
    """
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.shape[2] < 3:
        raise ValueError("input must be a color image")
    bgr = image_bgr[:, :, :3]
    channel_min = np.min(bgr, axis=2)
    channel_range = np.max(bgr, axis=2) - channel_min
    near_white = ((channel_min >= 210) & (channel_range <= 24)).astype(np.uint8)
    component_count, labels = cv2.connectedComponents(near_white, connectivity=8)
    border_labels = set(np.unique(labels[0, :]))
    border_labels.update(np.unique(labels[-1, :]))
    border_labels.update(np.unique(labels[:, 0]))
    border_labels.update(np.unique(labels[:, -1]))
    border_labels.discard(0)
    background = np.zeros(labels.shape, dtype=bool)
    for label in border_labels:
        if 0 < int(label) < component_count:
            background |= labels == label

    alpha = np.where(background, 0, 255).astype(np.uint8)
    alpha = cv2.GaussianBlur(alpha, (3, 3), 0.55)
    active = alpha > 8
    if not np.any(active):
        raise ValueError("no foreground was extracted")
    ys, xs = np.where(active)
    margin = 2
    y0, y1 = max(0, int(ys.min()) - margin), min(alpha.shape[0], int(ys.max()) + margin + 1)
    x0, x1 = max(0, int(xs.min()) - margin), min(alpha.shape[1], int(xs.max()) + margin + 1)
    return np.dstack([bgr, alpha])[y0:y1, x0:x1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract a transparent overlay asset")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source = cv2.imdecode(
        np.frombuffer(args.input.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    cutout = extract_connected_white_background(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", cutout)
    if not ok:
        raise RuntimeError("failed to encode extracted PNG")
    args.output.write_bytes(encoded.tobytes())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
