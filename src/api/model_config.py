from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from redis.exceptions import RedisError

from src.core.config import Settings, get_settings
from src.services.ai_model_config import load_ai_model_settings, save_ai_model_settings

router = APIRouter()
SettingsDep = Annotated[Settings, Depends(get_settings)]


class AIModelConfigResponse(BaseModel):
    enabled: bool
    provider: str
    base_url: str
    model: str
    api_key_configured: bool


class UpdateAIModelConfigRequest(BaseModel):
    enabled: bool = True
    base_url: str = Field(min_length=8, max_length=512)
    model: str = Field(min_length=1, max_length=120)
    api_key: str | None = Field(default=None, min_length=1, max_length=1024)

    @field_validator("base_url", "model")
    @classmethod
    def strip_required_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


def _response(settings: Settings) -> AIModelConfigResponse:
    return AIModelConfigResponse(
        enabled=settings.ai_tagging_enabled,
        provider=settings.ai_tagging_provider,
        base_url=settings.ai_tagging_base_url,
        model=settings.ai_tagging_model,
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
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key.strip() if payload.api_key else None,
        )
    except RedisError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="模型配置服务暂不可用") from exc
    return _response(updated)
