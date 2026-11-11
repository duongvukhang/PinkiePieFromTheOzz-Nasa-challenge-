"""MVP authentication and API token endpoints (email/password + Bearer)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import COMMON_501, not_implemented
from app.schemas.auth import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenRead,
    AuthTokenResponse,
    UserLoginRequest,
    UserRead,
    UserRegisterRequest,
)

# PHASE2: ORCID OAuth + email magic-link login endpoints deferred per ROADMAP §2
router = APIRouter(prefix="/auth", tags=["auth"], responses=COMMON_501)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(payload: UserRegisterRequest) -> UserRead:
    """Register a user account with email and password."""
    not_implemented()


@router.post("/login")
async def login(payload: UserLoginRequest) -> AuthTokenResponse:
    """Authenticate with email and password."""
    not_implemented()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> None:
    """Revoke current cookie session."""
    not_implemented()


@router.get("/me")
async def get_me() -> UserRead:
    """Return authenticated user profile."""
    not_implemented()


@router.post("/tokens", status_code=status.HTTP_201_CREATED)
async def create_api_token(payload: ApiTokenCreate) -> ApiTokenCreated:
    """Mint a scoped programmatic Bearer API token."""
    not_implemented()


@router.get("/tokens")
async def list_api_tokens() -> list[ApiTokenRead]:
    """List current user's API tokens."""
    not_implemented()


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(token_id: UUID) -> None:
    """Revoke an API token by id."""
    not_implemented()
