from __future__ import annotations

import json

import cv2
import numpy as np

from src.services.storage.keys import build_enhancement_work_object_key

PIPELINE_STATE_VERSION = 1


def pipeline_object_key(job_id: str, image_id: str, stage: str, extension: str) -> str:
    return build_enhancement_work_object_key(job_id, image_id, stage, extension)


def pipeline_state_object_key(job_id: str, image_id: str) -> str:
    return pipeline_object_key(job_id, image_id, "state", "json")


def new_pipeline_state() -> dict[str, object]:
    return {"version": PIPELINE_STATE_VERSION}


def encode_pipeline_state(state: dict[str, object]) -> bytes:
    return json.dumps(
        state,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def decode_pipeline_state(data: bytes) -> dict[str, object]:
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != PIPELINE_STATE_VERSION:
        raise ValueError("unsupported enhancement pipeline state")
    return payload


def encode_mask(mask: np.ndarray) -> bytes:
    if mask.ndim != 2:
        raise ValueError("watermark mask must be single-channel")
    ok, encoded = cv2.imencode(".png", mask)
    if not ok:
        raise ValueError("unable to encode watermark mask")
    return encoded.tobytes()


def decode_mask(data: bytes) -> np.ndarray:
    mask = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise ValueError("unable to decode watermark mask")
    return mask
