ARG RUNTIME_BASE=ghcr.io/zuixi01/tuxiangshibie-api@sha256:6ecb52b7792794e9f2a5a8c433dcd8b9c25ffbad9bcb071eaf058200618d2c24
FROM ${RUNTIME_BASE}

USER root

# Redaction inference dependencies and immutable, checksum-verified models are
# assembled in CI. Runtime hosts never download or build model artifacts.
COPY requirements-redaction.txt /tmp/requirements-redaction.txt
# RapidOCR declares the GUI OpenCV distribution. Slim production images must
# keep only the headless wheel or cv2 will require libxcb at import.
RUN pip install --no-cache-dir -r /tmp/requirements-redaction.txt \
    && rm /tmp/requirements-redaction.txt
RUN pip uninstall -y opencv-python \
    && pip install --no-cache-dir --force-reinstall --no-deps 'opencv-python-headless>=5.0.0'
COPY models /app/models
COPY scripts/fetch_redaction_models.py /app/scripts/fetch_redaction_models.py
COPY scripts/verify_redaction_supply_chain.py /app/scripts/verify_redaction_supply_chain.py
RUN python /app/scripts/fetch_redaction_models.py \
    && chown -R app:app /app/models

# The pinned runtime base already contains Python, application dependencies,
# PyTorch and the offline OpenCLIP model cache. A normal release only replaces
# application-owned files, so production hosts reuse the expensive base layers.
RUN rm -rf /app/src /app/profiles /app/alembic /app/alembic.ini

COPY --chown=app:app src /app/src
COPY --chown=app:app profiles /app/profiles
COPY --chown=app:app alembic.ini /app/alembic.ini
COPY --chown=app:app alembic /app/alembic
COPY --chown=app:app THIRD_PARTY_NOTICES.md /app/THIRD_PARTY_NOTICES.md
COPY --chown=app:app requirements-redaction.txt /app/requirements-redaction.txt
COPY --chown=app:app scripts/dedupe_celery_queue.py /app/scripts/dedupe_celery_queue.py

ENV HF_HUB_OFFLINE=1

USER app

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
