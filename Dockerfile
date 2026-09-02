ARG RUNTIME_BASE=ghcr.io/zuixi01/tuxiangshibie-api@sha256:6ecb52b7792794e9f2a5a8c433dcd8b9c25ffbad9bcb071eaf058200618d2c24
FROM ${RUNTIME_BASE}

USER root

# The pinned runtime base already contains Python, application dependencies,
# PyTorch and the offline OpenCLIP model cache. A normal release only replaces
# application-owned files, so production hosts reuse the expensive base layers.
RUN rm -rf /app/src /app/profiles /app/alembic /app/alembic.ini

COPY --chown=app:app src /app/src
COPY --chown=app:app profiles /app/profiles
COPY --chown=app:app alembic.ini /app/alembic.ini
COPY --chown=app:app alembic /app/alembic

ENV HF_HUB_OFFLINE=1

USER app

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
