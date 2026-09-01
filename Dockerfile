FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/model-cache/huggingface \
    HF_HUB_DISABLE_XET=1

WORKDIR /app

COPY pyproject.toml ./
COPY docker/package-placeholder/README.md ./README.md
COPY docker/package-placeholder/src ./src
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      torch==2.8.0 torchvision==0.23.0 \
    && pip install --no-cache-dir . \
    && python -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-32', pretrained='laion2b_s34b_b79k', device='cpu')" \
    && rm -rf /app/src

# Application changes stay above the expensive dependency/model layer so a
# normal release does not redownload PyTorch or OpenCLIP weights.
COPY src ./src
COPY profiles ./profiles
COPY alembic.ini ./
COPY alembic ./alembic

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && chown -R app:app /app

# Runtime containers are intentionally offline for model loading. The immutable
# image must contain all weights so a processing job never blocks on a download.
ENV HF_HUB_OFFLINE=1

USER app

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
