from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from secrets import compare_digest, token_hex
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import Principal, require_admin, require_api_key
from src.core.config import Settings, get_settings
from src.db.session import get_db_session
from src.models.user_account import UserAccount
from src.models.user_session import UserSession
from src.schemas.auth import (
    CreateUserRequest,
    LoginRequest,
    PasswordRequest,
    RegisterRequest,
    SessionResponse,
    UpdateUserRequest,
    UserResponse,
)
from src.services.auth import (
    DUMMY_PASSWORD_HASH,
    csrf_token_for_session,
    generate_session_token,
    hash_password,
    hash_session_token,
    registration_retry_after,
    utc_now,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
DbDep = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
PrincipalDep = Annotated[Principal, Depends(require_api_key)]
AdminDep = Annotated[Principal, Depends(require_admin)]


def _session_response(
    user: UserAccount,
    raw_token: str,
    expires_at: datetime,
    settings: Settings,
) -> SessionResponse:
    return SessionResponse(
        user=UserResponse.model_validate(user),
        csrf_token=csrf_token_for_session(raw_token, settings.auth_session_secret),
        expires_at=expires_at,
    )


def _set_session_cookie(
    request: Request,
    response: Response,
    raw_token: str,
    settings: Settings,
) -> None:
    max_age = settings.auth_session_ttl_hours * 60 * 60
    forwarded_scheme = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=raw_token,
        max_age=max_age,
        httponly=True,
        secure=request.url.scheme == "https" or forwarded_scheme == "https",
        samesite="lax",
        path="/",
    )


async def _create_user(
    payload: RegisterRequest,
    db: AsyncSession,
    *,
    role: str,
) -> UserAccount:
    password_hash = await asyncio.to_thread(hash_password, payload.password)
    user = UserAccount(
        id=token_hex(20),
        username=payload.username,
        display_name=payload.display_name,
        password_hash=password_hash,
        role=role,
        is_active=True,
        failed_login_count=0,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在") from exc
    await db.refresh(user)
    return user


async def _start_session(
    user: UserAccount,
    request: Request,
    response: Response,
    db: AsyncSession,
    settings: Settings,
) -> SessionResponse:
    now = utc_now()
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    raw_token = generate_session_token()
    expires_at = now + timedelta(hours=settings.auth_session_ttl_hours)
    db.add(
        UserSession(
            token_hash=hash_session_token(raw_token),
            user_id=user.id,
            expires_at=expires_at,
        )
    )
    await db.execute(delete(UserSession).where(UserSession.expires_at <= now))
    await db.commit()
    await db.refresh(user)
    _set_session_cookie(request, response, raw_token, settings)
    return _session_response(user, raw_token, expires_at, settings)


async def _ensure_active_admin_remains(
    db: AsyncSession,
    user: UserAccount,
    *,
    next_role: str,
    next_active: bool,
) -> None:
    if user.role != "admin" or not user.is_active:
        return
    if next_role == "admin" and next_active:
        return
    admin_count = await db.scalar(
        select(func.count()).select_from(UserAccount).where(
            UserAccount.role == "admin",
            UserAccount.is_active.is_(True),
        )
    )
    if (admin_count or 0) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="不能停用或降级最后一个管理员账号",
        )


@router.post("/login", response_model=SessionResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
) -> SessionResponse:
    user = await db.scalar(
        select(UserAccount).where(UserAccount.username == payload.username).with_for_update()
    )
    now = utc_now()
    if user is not None and user.locked_until is not None and user.locked_until > now:
        retry_after = max(1, int((user.locked_until - now).total_seconds()))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录尝试过多，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )

    password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_matches = await asyncio.to_thread(verify_password, payload.password, password_hash)
    if user is None or not password_matches:
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.auth_failed_login_limit:
                user.failed_login_count = 0
                user.locked_until = now + timedelta(minutes=settings.auth_lockout_minutes)
            await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")

    return await _start_session(user, request, response, db, settings)


@router.post("/register", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
) -> SessionResponse:
    client_identifier = request.headers.get("x-real-ip") or (
        request.client.host if request.client is not None else "unknown"
    )
    retry_after = await registration_retry_after(settings, client_identifier)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="注册请求过于频繁，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )
    user = await _create_user(payload, db, role="operator")
    return await _start_session(user, request, response, db, settings)


@router.get("/me", response_model=SessionResponse)
async def current_user(principal: PrincipalDep, settings: SettingsDep) -> SessionResponse:
    if principal.user is None or principal.session_token is None or principal.session_expires_at is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请使用账号登录")
    return _session_response(
        principal.user,
        principal.session_token,
        principal.session_expires_at,
        settings,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    principal: PrincipalDep,
    db: DbDep,
    settings: SettingsDep,
) -> None:
    if principal.session_token is not None:
        await db.execute(
            delete(UserSession).where(
                UserSession.token_hash == hash_session_token(principal.session_token)
            )
        )
        await db.commit()
    response.delete_cookie(settings.auth_session_cookie_name, path="/")


@router.post("/bootstrap", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def bootstrap_admin(
    payload: CreateUserRequest,
    db: DbDep,
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header()] = None,
) -> UserAccount:
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务端 API_KEY 未配置，无法初始化管理员",
        )
    if x_api_key is None or not compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API Key 无效")
    admin_count = await db.scalar(
        select(func.count()).select_from(UserAccount).where(UserAccount.role == "admin")
    )
    if admin_count:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="系统已经完成账号初始化")
    return await _create_user(payload, db, role="admin")


@router.get("/users", response_model=list[UserResponse])
async def list_users(_principal: AdminDep, db: DbDep) -> list[UserAccount]:
    users = await db.scalars(select(UserAccount).order_by(UserAccount.created_at, UserAccount.id))
    return list(users)


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest,
    _principal: AdminDep,
    db: DbDep,
) -> UserAccount:
    return await _create_user(payload, db, role=payload.role)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    _principal: AdminDep,
    db: DbDep,
) -> UserAccount:
    user = await db.get(UserAccount, user_id, with_for_update=True)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    next_role = payload.role or user.role
    next_active = payload.is_active if payload.is_active is not None else user.is_active
    await _ensure_active_admin_remains(
        db,
        user,
        next_role=next_role,
        next_active=next_active,
    )
    if payload.display_name is not None:
        user.display_name = payload.display_name
    user.role = next_role
    user.is_active = next_active
    if not next_active:
        await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: str,
    payload: PasswordRequest,
    _principal: AdminDep,
    db: DbDep,
) -> None:
    user = await db.get(UserAccount, user_id, with_for_update=True)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    user.password_hash = await asyncio.to_thread(hash_password, payload.password)
    user.failed_login_count = 0
    user.locked_until = None
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await db.commit()
