from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

UserRole = Literal["admin", "operator"]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: Any) -> Any:
        return value.strip().casefold() if isinstance(value, str) else value


class PasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=10, max_length=128)


class RegisterRequest(PasswordRequest):
    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._+@-]*$",
    )
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: Any) -> Any:
        return value.strip().casefold() if isinstance(value, str) else value

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        return value.strip()


class CreateUserRequest(RegisterRequest):
    role: UserRole = "operator"


class UpdateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: UserRole | None = None
    is_active: bool | None = None

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class SessionResponse(BaseModel):
    user: UserResponse
    csrf_token: str
    expires_at: datetime
