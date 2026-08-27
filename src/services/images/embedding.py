from __future__ import annotations

import asyncio
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageOps

from src.core.config import Settings


class ImageEmbeddingError(RuntimeError):
    pass


class OpenClipImageEmbedder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def embed(self, image_bytes: bytes) -> list[float]:
        return await asyncio.to_thread(self._embed_sync, image_bytes)

    def _embed_sync(self, image_bytes: bytes) -> list[float]:
        try:
            torch, model, preprocess = _load_model(
                self.settings.image_embedding_model,
                self.settings.image_embedding_pretrained,
                self.settings.inference_device,
                self.settings.inference_cpu_threads,
            )
            with Image.open(BytesIO(image_bytes)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                tensor = preprocess(image).unsqueeze(0).to(next(model.parameters()).device)
            with torch.no_grad():
                features = model.encode_image(tensor)
                features = features / features.norm(dim=-1, keepdim=True)
            values = features[0].cpu().tolist()
        except Exception as exc:
            raise ImageEmbeddingError("本地图片向量模型不可用") from exc
        if len(values) != 512:
            raise ImageEmbeddingError(f"图片向量维度异常：{len(values)}")
        return [float(value) for value in values]


@lru_cache(maxsize=2)
def _load_model(model_name: str, pretrained: str, requested_device: str, cpu_threads: int):
    try:
        import open_clip
        import torch
    except ImportError as exc:
        raise ImageEmbeddingError("未安装 open-clip-torch") from exc
    device = requested_device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        raise ImageEmbeddingError("已配置 GPU 推理，但当前环境没有可用 CUDA 设备")
    if device == "cpu":
        torch.set_num_threads(max(1, cpu_threads))
    model, _, preprocess = open_clip.create_model_and_transforms(
        model_name, pretrained=pretrained, device=device
    )
    model.eval()
    return torch, model, preprocess
