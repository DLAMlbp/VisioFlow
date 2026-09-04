FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

LABEL org.opencontainers.image.source="https://github.com/zuixi01/tuxiangshibie"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/model-cache/huggingface \
    HF_HUB_DISABLE_XET=1

WORKDIR /app

# Build a self-contained runtime. The prior GHCR base image was not readable
# by GitHub Actions, preventing any release image from being published.
COPY pyproject.toml ./
COPY docker/package-placeholder/README.md ./README.md
COPY docker/package-placeholder/src ./src
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      torch==2.8.0 torchvision==0.23.0 \
    && pip install --no-cache-dir . \
    && python -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-32', pretrained='laion2b_s34b_b79k', device='cpu')" \
    && rm -rf /app/src

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app

# Redaction inference dependencies and immutable, checksum-verified models are
# assembled in CI. Runtime hosts never download or build model artifacts.
COPY requirements-redaction.txt /tmp/requirements-redaction.txt
RUN pip install --no-cache-dir -r /tmp/requirements-redaction.txt \
    && rm /tmp/requirements-redaction.txt \
    && pip uninstall -y opencv-python \
    && pip install --no-cache-dir --force-reinstall --no-deps 'opencv-python-headless>=5.0.0'
COPY models /app/models
COPY scripts/fetch_redaction_models.py /app/scripts/fetch_redaction_models.py
COPY scripts/verify_redaction_supply_chain.py /app/scripts/verify_redaction_supply_chain.py
RUN python /app/scripts/fetch_redaction_models.py \
    && chown -R app:app /app/models

COPY --chown=app:app src /app/src
COPY --chown=app:app profiles /app/profiles
COPY --chown=app:app assets /app/assets
COPY --chown=app:app alembic.ini /app/alembic.ini
COPY --chown=app:app alembic /app/alembic
COPY --chown=app:app THIRD_PARTY_NOTICES.md /app/THIRD_PARTY_NOTICES.md
COPY --chown=app:app requirements-redaction.txt /app/requirements-redaction.txt
COPY --chown=app:app scripts/dedupe_celery_queue.py /app/scripts/dedupe_celery_queue.py

ENV HF_HUB_OFFLINE=1

USER app

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
