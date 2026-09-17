from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from src.api import user_auth as user_auth_api
from src.api.auth import require_api_key
from src.core.config import Settings, get_settings
from src.db.session import get_db_session
from src.main import app
from src.models.user_account import UserAccount
from src.models.user_session import UserSession
from src.schemas.auth import CreateUserRequest, RegisterRequest
from src.services.auth import (
    csrf_token_for_session,
    hash_password,
    hash_session_token,
    registration_retry_after,
    utc_now,
    verify_password,
)


class _SessionDb:
    def __init__(self, session: UserSession | None = None):
        self.session = session
        self.deleted = None
        self.committed = False
        self.added = []

    async def scalar(self, _statement):
        return self.session

    async def delete(self, value):
        self.deleted = value

    async def execute(self, _statement):
        return None

    def add(self, value):
        self.added.append(value)

    async def refresh(self, _value):
        return None

    async def commit(self):
        self.committed = True


def _request(
    method: str = "GET",
    *,
    token: str | None = None,
    client_host: str = "127.0.0.1",
) -> Request:
    headers = [] if token is None else [(b"cookie", f"visioflow_session={token}".encode())]
    return Request({
        "type": "http",
        "method": method,
        "path": "/api/test",
        "headers": headers,
        "client": (client_host, 12345),
    })


def _settings() -> Settings:
    return Settings(
        api_key="service-api-key",
        auth_session_secret="s" * 32,
        _env_file=None,
    )


def _active_session(token: str) -> UserSession:
    now = utc_now()
    user = UserAccount(
        id="user_1",
        username="operator",
        display_name="操作员",
        password_hash="unused",
        role="operator",
        is_active=True,
        failed_login_count=0,
        created_at=now,
        updated_at=now,
    )
    session = UserSession(
        token_hash=hash_session_token(token),
        user_id=user.id,
        expires_at=now + timedelta(hours=1),
        created_at=now,
    )
    session.user = user
    return session


def test_scrypt_password_hash_round_trip() -> None:
    encoded = hash_password("a-long-password")

    assert encoded.startswith("scrypt$")
    assert verify_password("a-long-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_create_user_request_normalizes_username() -> None:
    payload = CreateUserRequest(
        username=" Admin.User ",
        display_name=" 管理员 ",
        password="secure-passphrase",
        role="admin",
    )

    assert payload.username == "admin.user"
    assert payload.display_name == "管理员"


def test_register_request_accepts_email_login_identifier() -> None:
    payload = RegisterRequest(
        username=" 2940891991@QQ.COM ",
        display_name=" 新用户 ",
        password="secure-passphrase",
    )

    assert payload.username == "2940891991@qq.com"
    assert payload.display_name == "新用户"


@pytest.mark.asyncio
async def test_registration_rate_limit_uses_shared_redis_window(monkeypatch) -> None:
    class _RateRedis:
        def __init__(self):
            self.count = 0
            self.closed = False

        async def incr(self, _key):
            self.count += 1
            return self.count

        async def expire(self, _key, _seconds):
            return True

        async def ttl(self, _key):
            return 37

        async def aclose(self):
            self.closed = True

    redis = _RateRedis()
    monkeypatch.setattr("src.services.auth.Redis.from_url", lambda *_args, **_kwargs: redis)
    settings = Settings(auth_registration_rate_limit_per_minute=2, _env_file=None)

    assert await registration_retry_after(settings, "192.0.2.10") is None
    assert await registration_retry_after(settings, "192.0.2.10") is None
    assert await registration_retry_after(settings, "192.0.2.10") == 37
    assert redis.closed


@pytest.mark.asyncio
async def test_api_key_remains_available_for_service_clients() -> None:
    principal = await require_api_key(
        _request(),
        _settings(),
        _SessionDb(),
        x_api_key="service-api-key",
    )

    assert principal.auth_type == "api_key"
    assert principal.role == "admin"


@pytest.mark.asyncio
async def test_local_web_proxy_can_open_the_workspace_without_a_login() -> None:
    principal = await require_api_key(
        _request(client_host="127.0.0.1"),
        _settings(),
        _SessionDb(),
        x_visioflow_web_access="1",
    )

    assert principal.auth_type == "web_proxy"
    assert principal.role == "admin"


@pytest.mark.asyncio
async def test_public_client_cannot_spoof_the_web_proxy_header() -> None:
    with pytest.raises(HTTPException) as unauthorized:
        await require_api_key(
            _request(client_host="8.8.8.8"),
            _settings(),
            _SessionDb(),
            x_visioflow_web_access="1",
        )

    assert unauthorized.value.status_code == 401


@pytest.mark.asyncio
async def test_session_authentication_requires_csrf_for_writes() -> None:
    token = "session-token"
    settings = _settings()
    db = _SessionDb(_active_session(token))

    with pytest.raises(HTTPException) as missing_csrf:
        await require_api_key(_request("POST", token=token), settings, db)
    assert missing_csrf.value.status_code == 403

    principal = await require_api_key(
        _request("POST", token=token),
        settings,
        db,
        x_csrf_token=csrf_token_for_session(token, settings.auth_session_secret),
    )
    assert principal.auth_type == "session"
    assert principal.user is db.session.user


@pytest.mark.asyncio
async def test_expired_session_is_removed() -> None:
    token = "expired-token"
    session = _active_session(token)
    session.expires_at = utc_now() - timedelta(seconds=1)
    db = _SessionDb(session)

    with pytest.raises(HTTPException) as expired:
        await require_api_key(_request(token=token), _settings(), db)

    assert expired.value.status_code == 401
    assert db.deleted is session
    assert db.committed


@pytest.mark.asyncio
async def test_disabled_account_session_is_revoked() -> None:
    token = "disabled-token"
    session = _active_session(token)
    session.user.is_active = False
    db = _SessionDb(session)

    with pytest.raises(HTTPException) as disabled:
        await require_api_key(_request(token=token), _settings(), db)

    assert disabled.value.status_code == 401
    assert db.committed


@pytest.mark.asyncio
async def test_login_creates_http_only_session_and_csrf_token() -> None:
    now = utc_now()
    user = UserAccount(
        id="user_admin",
        username="admin",
        display_name="系统管理员",
        password_hash=hash_password("secure-passphrase"),
        role="admin",
        is_active=True,
        failed_login_count=0,
        created_at=now,
        updated_at=now,
    )
    db = _SessionDb()
    db.session = user  # type: ignore[assignment]

    async def database_override():
        yield db

    app.dependency_overrides[get_db_session] = database_override
    app.dependency_overrides[get_settings] = _settings
    try:
        response = TestClient(app).post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "secure-passphrase"},
            headers={"X-Forwarded-Proto": "https"},
        )
    finally:
        app.dependency_overrides.clear()

    created_session = db.added[0]
    session_response = response.json()
    assert response.status_code == 200
    assert isinstance(created_session, UserSession)
    assert created_session.user_id == user.id
    assert session_response["user"]["username"] == "admin"
    assert len(session_response["csrf_token"]) == 64
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]


def test_public_registration_creates_operator_and_starts_session(monkeypatch) -> None:
    now = utc_now()
    db = _SessionDb()

    async def database_override():
        yield db

    async def no_rate_limit(_settings, _client_identifier):
        return None

    async def create_operator(payload, _db, *, role):
        assert payload.username == "2940891991@qq.com"
        assert role == "operator"
        return UserAccount(
            id="user_registered",
            username=payload.username,
            display_name=payload.display_name,
            password_hash=hash_password(payload.password),
            role=role,
            is_active=True,
            failed_login_count=0,
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setattr("src.api.user_auth.registration_retry_after", no_rate_limit)
    monkeypatch.setattr("src.api.user_auth._create_user", create_operator)
    app.dependency_overrides[get_db_session] = database_override
    app.dependency_overrides[get_settings] = _settings
    try:
        response = TestClient(app).post(
            "/api/v1/auth/register",
            json={
                "username": "2940891991@qq.com",
                "display_name": "新用户",
                "password": "secure-passphrase",
            },
            headers={"X-Real-IP": "192.0.2.10"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["user"]["role"] == "operator"
    assert response.json()["user"]["username"] == "2940891991@qq.com"
    assert isinstance(db.added[0], UserSession)
    assert "HttpOnly" in response.headers["set-cookie"]


def test_public_registration_rejects_role_escalation() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(
            username="attacker",
            display_name="Attacker",
            password="secure-passphrase",
            role="admin",  # type: ignore[call-arg]
        )


@pytest.mark.asyncio
async def test_bootstrap_remains_available_when_only_operators_exist(monkeypatch) -> None:
    class _AdminCountDb(_SessionDb):
        statement = None

        async def scalar(self, statement):
            self.statement = statement
            return 0

    db = _AdminCountDb()
    created_role = None

    async def create_admin(payload, _db, *, role):
        nonlocal created_role
        created_role = role
        return payload

    monkeypatch.setattr(user_auth_api, "_create_user", create_admin)

    await user_auth_api.bootstrap_admin(
        CreateUserRequest(
            username="admin",
            display_name="系统管理员",
            password="secure-passphrase",
            role="admin",
        ),
        db,
        _settings(),
        x_api_key="service-api-key",
    )

    assert created_role == "admin"
    assert "user_accounts.role" in str(db.statement)
