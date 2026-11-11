"""Authentication and API token contracts (MVP email/password + bearer tokens)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class UserRegisterRequest(BaseModel):
    """Email/password account registration payload."""

    email: str = Field(pattern=_EMAIL_RE)
    password: str = Field(min_length=8, max_length=256)
    display_name: str | None = Field(default=None, max_length=120)


class UserLoginRequest(BaseModel):
    """Email/password login credentials."""

    email: str = Field(pattern=_EMAIL_RE)
    password: str = Field(min_length=1, max_length=256)


class UserRead(BaseModel):
    """Public user profile (never exposes password hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str | None = None
    role: Literal["user", "scientist", "admin"]
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AuthTokenResponse(BaseModel):
    """Session/Bearer login response."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserRead


class ApiTokenCreate(BaseModel):
    """Request to mint a scoped API token."""

    name: str | None = Field(default=None, max_length=100)
    scopes: list[str] = Field(min_length=1)


class ApiTokenRead(BaseModel):
    """Stored API token metadata without raw secret."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str | None = None
    scopes: list[str]
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiTokenCreated(ApiTokenRead):
    """API token creation response returning plaintext token once."""

    token: str
