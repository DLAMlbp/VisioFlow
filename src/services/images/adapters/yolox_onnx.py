from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Lock
from typing import Any

import cv2
import numpy as np

from src.services.images.logo_detector import LogoDetection
from src.services.profiles import LogoMosaicConfig


def _preprocess(image_bgr: np.ndarray, input_size: tuple[int, int]) -> tuple[np.ndarray, float]:
    """YOLOX Apache-2.0 ONNX demo preprocessing, kept behavior-compatible."""
    padded = np.full((input_size[0], input_size[1], 3), 114, dtype=np.uint8)
    ratio = min(input_size[0] / image_bgr.shape[0], input_size[1] / image_bgr.shape[1])
    resized = cv2.resize(
        image_bgr,
        (round(image_bgr.shape[1] * ratio), round(image_bgr.shape[0] * ratio)),
        interpolation=cv2.INTER_LINEAR,
    )
    padded[: resized.shape[0], : resized.shape[1]] = resized
    tensor = np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32)
    return tensor, ratio


def _postprocess(outputs: np.ndarray, input_size: tuple[int, int]) -> np.ndarray:
    """Decode YOLOX anchor-free grid outputs, adapted from the official demo."""
    grids: list[np.ndarray] = []
    expanded_strides: list[np.ndarray] = []
    for stride in (8, 16, 32):
        height, width = input_size[0] // stride, input_size[1] // stride
        xv, yv = np.meshgrid(np.arange(width), np.arange(height))
        grid = np.stack((xv, yv), axis=2).reshape(1, -1, 2)
        grids.append(grid)
        expanded_strides.append(np.full((*grid.shape[:2], 1), stride))
    grid_array = np.concatenate(grids, axis=1)
    stride_array = np.concatenate(expanded_strides, axis=1)
    decoded = outputs.copy()
    decoded[..., :2] = (decoded[..., :2] + grid_array) * stride_array
    decoded[..., 2:4] = np.exp(decoded[..., 2:4]) * stride_array
    return decoded


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    """Single-class NumPy NMS adapted from YOLOX's Apache-2.0 demo."""
    if len(boxes) == 0:
        return []
    x0, y0, x1, y1 = (boxes[:, index] for index in range(4))
    areas = np.maximum(0, x1 - x0 + 1) * np.maximum(0, y1 - y0 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        index = int(order[0])
        keep.append(index)
        xx0 = np.maximum(x0[index], x0[order[1:]])
        yy0 = np.maximum(y0[index], y0[order[1:]])
        xx1 = np.minimum(x1[index], x1[order[1:]])
        yy1 = np.minimum(y1[index], y1[order[1:]])
        intersection = np.maximum(0, xx1 - xx0 + 1) * np.maximum(0, yy1 - yy0 + 1)
        overlap = intersection / np.maximum(
            areas[index] + areas[order[1:]] - intersection, 1e-6
        )
        order = order[np.where(overlap <= threshold)[0] + 1]
    return keep


class YoloXOnnxLogoDetector:
    """Lazy, checksum-verified YOLOX ONNX detector for Dangjia logos."""

    def __init__(self, manifest_path: str | Path, *, intra_op_threads: int = 1) -> None:
        self.manifest_path = Path(manifest_path)
        self.intra_op_threads = intra_op_threads
        self._manifest = self._read_manifest()
        self.model_version = str(self._manifest["id"])
        self._session: Any | None = None
        self._lock = Lock()

    def _read_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            raise RuntimeError(f"logo model manifest is missing: {self.manifest_path}")
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        for field in ("id", "model", "input_size", "classes"):
            if field not in payload:
                raise RuntimeError(f"logo model manifest is missing field: {field}")
        return payload

    def _model_path(self) -> Path:
        return self.manifest_path.parent / str(self._manifest["model"])

    def _load_session(self):
        if self._session is not None:
            return self._session
        with self._lock:
            if self._session is not None:
                return self._session
            model_path = self._model_path()
            if not model_path.is_file():
                raise RuntimeError(f"logo ONNX model is missing: {model_path}")
            expected_sha = str(self._manifest.get("sha256") or "").lower()
            if expected_sha:
                actual_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
                if actual_sha != expected_sha:
                    raise RuntimeError("logo ONNX model checksum mismatch")
            try:
                import onnxruntime as ort
            except ImportError:
                raise RuntimeError("onnxruntime is not installed") from None
            options = ort.SessionOptions()
            options.intra_op_num_threads = self.intra_op_threads
            options.inter_op_num_threads = 1
            self._session = ort.InferenceSession(
                str(model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            return self._session

    def detect(
        self, image_bgr: np.ndarray, config: LogoMosaicConfig
    ) -> list[LogoDetection]:
        session = self._load_session()
        input_size_values = self._manifest["input_size"]
        input_size = (int(input_size_values[0]), int(input_size_values[1]))
        tensor, ratio = _preprocess(image_bgr, input_size)
        input_name = session.get_inputs()[0].name
        outputs = session.run(None, {input_name: tensor[None]})[0]
        predictions = _postprocess(np.asarray(outputs), input_size)[0]
        boxes = predictions[:, :4]
        scores = predictions[:, 4:5] * predictions[:, 5:]
        class_indices = scores.argmax(axis=1)
        class_scores = scores[np.arange(len(class_indices)), class_indices]
        selected = class_scores >= config.confidence
        if not np.any(selected):
            return []
        boxes = boxes[selected]
        class_scores = class_scores[selected]
        class_indices = class_indices[selected]
        boxes_xyxy = np.empty_like(boxes)
        boxes_xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
        boxes_xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
        boxes_xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
        boxes_xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
        boxes_xyxy /= ratio
        keep = _nms(boxes_xyxy, class_scores, config.nms_iou)
        classes = list(self._manifest["classes"])
        detections: list[LogoDetection] = []
        for index in keep:
            class_index = int(class_indices[index])
            class_name = classes[class_index] if class_index < len(classes) else "dangjia_logo"
            x0, y0, x1, y1 = boxes_xyxy[index]
            detections.append(
                LogoDetection(
                    box=(round(x0), round(y0), round(x1), round(y1)),
                    confidence=float(class_scores[index]),
                    label="dangjia_logo",
                    product_logo=class_name == "dangjia_product_logo",
                )
            )
        return detections

