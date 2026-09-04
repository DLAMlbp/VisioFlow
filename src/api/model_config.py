from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError

from src.core.config import Settings, get_settings
from src.services.ai_model_config import load_ai_model_settings, save_ai_model_settings

router = APIRouter()
SettingsDep = Annotated[Settings, Depends(get_settings)]


class AIModelConfigResponse(BaseModel):
    enabled: bool
    api_key_configured: bool


class UpdateAIModelConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    api_key: str | None = Field(default=None, min_length=1, max_length=1024)


def _response(settings: Settings) -> AIModelConfigResponse:
    return AIModelConfigResponse(
        enabled=settings.ai_tagging_enabled,
        api_key_configured=bool(settings.ai_tagging_api_key),
    )


@router.get("/ai-model", response_model=AIModelConfigResponse)
async def get_ai_model_config(settings: SettingsDep) -> AIModelConfigResponse:
    return _response(load_ai_model_settings(settings))


@router.put("/ai-model", response_model=AIModelConfigResponse)
async def update_ai_model_config(
    payload: UpdateAIModelConfigRequest,
    settings: SettingsDep,
) -> AIModelConfigResponse:
    try:
        updated = save_ai_model_settings(
            settings,
            enabled=payload.enabled,
            api_key=payload.api_key.strip() if payload.api_key else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except RedisError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="模型配置服务暂不可用") from exc
    return _response(updated)
