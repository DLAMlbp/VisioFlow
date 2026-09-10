from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from secrets import compare_digest
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.db.session import get_db_session
from src.models.user_account import UserAccount
from src.models.user_session import UserSession
from src.services.auth import csrf_token_for_session, hash_session_token, utc_now


@dataclass(frozen=True)
class Principal:
    auth_type: Literal["session", "api_key"]
    role: Literal["admin", "operator"]
    user: UserAccount | None = None
    session_token: str | None = None
    session_expires_at: datetime | None = None


async def require_api_key(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    x_api_key: Annotated[str | None, Header()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> Principal:
    if x_api_key is not None:
        if settings.api_key and compare_digest(x_api_key, settings.api_key):
            return Principal(auth_type="api_key", role="admin")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API Key 无效")

    raw_token = request.cookies.get(settings.auth_session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

    token_hash = hash_session_token(raw_token)
    session = await db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))
    now = utc_now()
    if session is None or session.expires_at <= now:
        if session is not None:
            await db.delete(session)
            await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")
    if not session.user.is_active:
        await db.execute(delete(UserSession).where(UserSession.user_id == session.user_id))
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号已停用")

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        expected_csrf = csrf_token_for_session(raw_token, settings.auth_session_secret)
        if x_csrf_token is None or not compare_digest(x_csrf_token, expected_csrf):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="请求校验失败，请刷新页面重试")

    return Principal(
        auth_type="session",
        role=session.user.role,  # type: ignore[arg-type]
        user=session.user,
        session_token=raw_token,
        session_expires_at=session.expires_at,
    )


async def require_admin(
    principal: Annotated[Principal, Depends(require_api_key)],
) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return principal


async def require_integration_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.integration_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务端 INTEGRATION_API_KEY 未配置",
        )
    if x_api_key is None or not compare_digest(x_api_key, settings.integration_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="集成 API Key 无效")
