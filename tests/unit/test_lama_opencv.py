import cv2
import numpy as np

from src.services.images.adapters import lama_opencv


class _FakeNetwork:
    def __init__(self) -> None:
        self.inputs: dict[str, np.ndarray] = {}

    def setInput(self, value: np.ndarray, name: str) -> None:
        self.inputs[name] = value

    def forward(self) -> np.ndarray:
        assert self.inputs["image"].shape == (1, 3, 512, 512)
        assert self.inputs["mask"].shape == (1, 1, 512, 512)
        return np.full((1, 3, 512, 512), 180, dtype=np.float32)


def test_lama_only_pastes_pixels_inside_mask(monkeypatch) -> None:
    network = _FakeNetwork()

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(lama_opencv, "_network", lambda model_path: (network, _Lock()))
    image = np.full((80, 140, 3), 25, dtype=np.uint8)
    mask = np.zeros((80, 140), dtype=np.uint8)
    cv2.rectangle(mask, (20, 30), (60, 50), 255, -1)

    output = lama_opencv.erase_lama_opencv(image, mask, model_path="fake.onnx")

    assert np.all(output[mask > 0] == 180)
    assert np.array_equal(output[mask == 0], image[mask == 0])
