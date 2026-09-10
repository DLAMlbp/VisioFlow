import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.api.router import api_router
from src.core.config import get_settings
from src.core.logging import configure_logging

configure_logging()
logger = logging.getLogger("image_intelligence.http")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Image Intelligence Service",
        version="0.1.0",
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url="/redoc" if settings.app_env != "production" else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[host.strip() for host in settings.trusted_hosts.split(",") if host.strip()],
    )

    @app.middleware("http")
    async def request_observability(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", "").strip()[:64] or uuid4().hex
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed method=%s path=%s request_id=%s",
                request.method,
                request.url.path,
                request_id,
            )
            raise
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed method=%s path=%s status=%s duration_ms=%s request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            round((time.perf_counter() - started) * 1000),
            request_id,
        )
        return response

    app.include_router(api_router)
    return app


app = create_app()
