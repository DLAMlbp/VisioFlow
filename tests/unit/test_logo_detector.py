import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.services.images.adapters.rapidocr import OcrTextLine
from src.services.images.adapters.yolox_onnx import (
    YoloXOnnxLogoDetector,
    _nms,
    _preprocess,
)
from src.services.images.logo_detector import (
    HybridLogoDetector,
    LogoDetection,
    RapidOcrDangjiaLogoDetector,
)
from src.services.profiles import LogoMosaicConfig


class _Session:
    def __init__(self, outputs: np.ndarray) -> None:
        self.outputs = outputs

    def get_inputs(self):
        return [SimpleNamespace(name="images")]

    def run(self, output_names, inputs):
        del output_names
        assert inputs["images"].shape == (1, 3, 416, 416)
        return [self.outputs]


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "id": "test-yolox",
                "model": "missing.onnx",
                "input_size": [416, 416],
                "classes": ["dangjia_logo", "dangjia_product_logo"],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_yolox_preprocess_keeps_aspect_ratio_and_padding() -> None:
    image = np.full((100, 200, 3), 33, dtype=np.uint8)

    tensor, ratio = _preprocess(image, (416, 416))

    assert tensor.shape == (3, 416, 416)
    assert ratio == 2.08
    assert np.all(tensor[:, 208:, :] == 114)


def test_yolox_numpy_nms_suppresses_overlapping_box() -> None:
    boxes = np.asarray([[0, 0, 20, 20], [1, 1, 21, 21], [30, 30, 40, 40]], dtype=float)
    scores = np.asarray([0.9, 0.8, 0.7], dtype=float)

    assert _nms(boxes, scores, 0.5) == [0, 2]


def test_yolox_detector_decodes_single_brand_and_product_classes(tmp_path: Path) -> None:
    outputs = np.zeros((1, 3549, 7), dtype=np.float32)
    outputs[0, 0, :4] = [4, 4, np.log(4), np.log(3)]
    outputs[0, 0, 4:] = [1, 0.9, 0.1]
    outputs[0, 1, :4] = [12, 4, np.log(3), np.log(2)]
    outputs[0, 1, 4:] = [1, 0.1, 0.8]
    detector = YoloXOnnxLogoDetector(_manifest(tmp_path))
    detector._session = _Session(outputs)

    detections = detector.detect(
        np.full((100, 100, 3), 90, dtype=np.uint8),
        LogoMosaicConfig(enabled=True, confidence=0.45),
    )

    assert len(detections) == 2
    assert detections[0].label == "dangjia_logo"
    assert {item.product_logo for item in detections} == {False, True}


def test_yolox_missing_model_fails_with_actionable_message(tmp_path: Path) -> None:
    detector = YoloXOnnxLogoDetector(_manifest(tmp_path))

    try:
        detector.detect(
            np.full((100, 100, 3), 90, dtype=np.uint8),
            LogoMosaicConfig(enabled=True),
        )
    except RuntimeError as exc:
        assert "model is missing" in str(exc)
    else:
        raise AssertionError("missing model must not silently produce no detections")


def test_rapidocr_logo_detector_matches_brand_and_covers_left_icon(monkeypatch) -> None:
    brand = OcrTextLine(
        polygon=np.asarray([[80, 30], [180, 30], [180, 55], [80, 55]], dtype=np.float32),
        text="当家APP",
        confidence=0.98,
    )
    ordinary = OcrTextLine(
        polygon=np.asarray([[10, 80], [190, 80], [190, 110], [10, 110]], dtype=np.float32),
        text="长沙靠谱施工队",
        confidence=0.99,
    )
    monkeypatch.setattr(
        "src.services.images.adapters.rapidocr.detect_text_lines",
        lambda image: [brand, ordinary],
    )

    detections = RapidOcrDangjiaLogoDetector().detect(
        np.zeros((150, 220, 3), dtype=np.uint8), LogoMosaicConfig(enabled=True)
    )

    assert len(detections) == 1
    assert detections[0].box[0] < 80
    assert detections[0].box[3] > 55


def test_rapidocr_rotated_shirt_logo_uses_oriented_text_height(monkeypatch) -> None:
    shirt_brand = OcrTextLine(
        polygon=np.asarray(
            [[681, 947], [838, 911], [854, 979], [696, 1015]], dtype=np.float32
        ),
        text="当家AF",
        confidence=0.9335,
    )
    monkeypatch.setattr(
        "src.services.images.adapters.rapidocr.detect_text_lines",
        lambda image: [shirt_brand],
    )

    detections = RapidOcrDangjiaLogoDetector().detect(
        np.zeros((1440, 1080, 3), dtype=np.uint8), LogoMosaicConfig(enabled=True)
    )

    assert len(detections) == 1
    x0, y0, x1, y1 = detections[0].box
    assert 600 <= x0 < 681
    assert 890 <= y0 < 947
    assert x1 == 854
    assert 1015 < y1 <= 1050


def test_hybrid_logo_detector_falls_back_when_optional_model_is_unavailable() -> None:
    class _Unavailable:
        model_version = "unavailable-model"

        def detect(self, image, config):
            raise RuntimeError("missing weights")

    class _Available:
        model_version = "ocr"

        def detect(self, image, config):
            return [LogoDetection((1, 2, 20, 30), 0.9)]

    detections = HybridLogoDetector([_Unavailable(), _Available()]).detect(
        np.zeros((40, 40, 3), dtype=np.uint8), LogoMosaicConfig(enabled=True)
    )

    assert [item.box for item in detections] == [(1, 2, 20, 30)]


def test_hybrid_logo_detector_does_not_mix_ocr_into_healthy_primary_result() -> None:
    calls: list[str] = []

    class _Primary:
        model_version = "validated-yolox"

        def detect(self, image, config):
            del image, config
            calls.append("primary")
            return []

    class _Fallback:
        model_version = "ocr"

        def detect(self, image, config):
            del image, config
            calls.append("fallback")
            return [LogoDetection((1, 2, 20, 30), 0.9)]

    detections = HybridLogoDetector([_Primary(), _Fallback()]).detect(
        np.zeros((40, 40, 3), dtype=np.uint8), LogoMosaicConfig(enabled=True)
    )

    assert detections == []
    assert calls == ["primary"]
