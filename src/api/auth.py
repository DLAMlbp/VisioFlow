from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from src.core.config import Settings, get_settings


async def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务端 API_KEY 未配置",
        )
    if x_api_key is None or not compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API Key 无效")
