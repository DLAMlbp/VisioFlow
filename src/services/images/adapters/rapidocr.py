from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from threading import Lock

import numpy as np


@dataclass(frozen=True)
class OcrTextLine:
    polygon: np.ndarray
    text: str
    confidence: float


@lru_cache(maxsize=1)
def _engine() -> tuple[object, Lock]:
    try:
        from rapidocr import EngineType, RapidOCR
    except ImportError:
        raise RuntimeError(
            "ROI OCR requires the pinned rapidocr and openvino redaction extras"
        ) from None
    params = {
        "Global.log_level": "error",
        "Det.engine_type": EngineType.OPENVINO,
        "Cls.engine_type": EngineType.OPENVINO,
        "Rec.engine_type": EngineType.OPENVINO,
    }
    return RapidOCR(params=params), Lock()


def detect_text_lines(image_bgr: np.ndarray) -> list[OcrTextLine]:
    """Return transient OCR lines without persisting image text."""
    engine, lock = _engine()
    with lock:
        output = engine(image_bgr)
    boxes = getattr(output, "boxes", None)
    texts = getattr(output, "txts", None)
    scores = getattr(output, "scores", None)
    if boxes is None or texts is None or scores is None:
        return []
    return [
        OcrTextLine(
            polygon=np.asarray(box, dtype=np.float32),
            text=str(text),
            confidence=float(score),
        )
        for box, text, score in zip(boxes, texts, scores, strict=False)
    ]


def detect_text_polygons(image_bgr: np.ndarray) -> list[np.ndarray]:
    """Detect text only in the caller-supplied protected ROI.

    RapidOCR reuses PaddleOCR models under Apache-2.0. Recognition output is not
    stored; only polygons are used to construct a local repair mask.
    """
    return [line.polygon for line in detect_text_lines(image_bgr)]
