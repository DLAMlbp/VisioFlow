from fastapi import APIRouter, Depends

from src.api.auth import require_api_key
from src.api.health import router as health_router
from src.api.integration import router as integration_router
from src.api.jobs import router as jobs_router
from src.api.model_config import router as model_config_router
from src.api.profiles import router as profiles_router
from src.api.uploads import router as uploads_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(
    jobs_router,
    prefix="/api/v1/image/jobs",
    tags=["image jobs"],
    dependencies=[Depends(require_api_key)],
)
api_router.include_router(
    uploads_router,
    prefix="/api/v1/uploads",
    tags=["uploads"],
    dependencies=[Depends(require_api_key)],
)
api_router.include_router(
    profiles_router,
    prefix="/api/v1",
    tags=["profiles"],
    dependencies=[Depends(require_api_key)],
)
api_router.include_router(
    model_config_router,
    prefix="/api/v1/settings",
    tags=["AI model settings"],
    dependencies=[Depends(require_api_key)],
)
api_router.include_router(
    integration_router,
    prefix="/api/v1/integration",
    tags=["third-party integration"],
    dependencies=[Depends(require_api_key)],
)
