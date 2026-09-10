from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.core.config import Settings

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_LENGTH = 32


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 512:
        raise ValueError("密码过长")
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password_bytes,
        salt=actual_salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_LENGTH,
    )
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(actual_salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        expected_bytes = base64.urlsafe_b64decode(expected.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.urlsafe_b64decode(salt.encode("ascii")),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected_bytes),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected_bytes)


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token_for_session(token: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"csrf:{token}".encode(),
        hashlib.sha256,
    ).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)


async def registration_retry_after(
    settings: Settings,
    client_identifier: str,
) -> int | None:
    """Return a retry delay when one client exceeds the fixed registration window."""
    digest = hashlib.sha256(client_identifier.encode("utf-8")).hexdigest()
    key = f"image_intelligence:auth:registration:{digest}"
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
        if count <= settings.auth_registration_rate_limit_per_minute:
            return None
        ttl = await redis.ttl(key)
        return max(1, ttl)
    except RedisError:
        # Account creation still has database uniqueness and password controls;
        # a transient Redis outage must not lock every new user out.
        return None
    finally:
        await redis.aclose()


DUMMY_PASSWORD_HASH = hash_password("invalid-password", salt=b"visioflow-dummy!")
