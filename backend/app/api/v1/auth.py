"""MVP authentication and API token endpoints (email/password + Bearer)."""

from __future__ import annotations

import os
import time
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    APIProblemError,
    get_auth_context,
    get_cache,
    get_store,
    require_auth,
    require_write,
)
from app.schemas.auth import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenRead,
    AuthTokenResponse,
    UserLoginRequest,
    UserRead,
    UserRegisterRequest,
)
from app.schemas.common import ProblemDetail
from app.services.auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_S,
    AuthContext,
    client_ip_from_request,
    generate_api_token,
    hash_password,
    mint_session_token,
    verify_password,
)
from app.services.cache import CacheBackend
from app.services.jobs import JobStore

# PHASE2: ORCID OAuth + email magic-link login endpoints deferred per ROADMAP §2
router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses={
        401: {"model": ProblemDetail},
        403: {"model": ProblemDetail},
        404: {"model": ProblemDetail},
        409: {"model": ProblemDetail},
        422: {"model": ProblemDetail},
    },
)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserRegisterRequest,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
) -> UserRead:
    """Register a user account with email and password."""
    email = payload.email.strip().lower()
    try:
        user = store.create_user(
            email,
            hash_password(payload.password),
            display_name=payload.display_name,
        )
    except ValueError as exc:
        raise APIProblemError(
            status.HTTP_409_CONFLICT,
            "Email Already Registered",
            f"An account for {email} already exists.",
            error_type="https://spherex.example/problems/email-exists",
        ) from exc
    store.record_audit(
        user.id,
        "user.register",
        entity_table="users",
        entity_id=str(user.id),
        after={"email": user.email},
        ip=client_ip_from_request(request),
    )
    return user


@router.post("/login")
async def login(
    payload: UserLoginRequest,
    request: Request,
    response: Response,
    store: Annotated[JobStore, Depends(get_store)],
) -> AuthTokenResponse:
    """Authenticate with email and password, setting HttpOnly cookie and returning Bearer."""
    found = store.get_user_by_email(payload.email)
    if found is None or not verify_password(found[1], payload.password):
        raise APIProblemError(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid Credentials",
            "Invalid email or password.",
            error_type="https://spherex.example/problems/invalid-credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = found[0]
    token = mint_session_token(user.id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_S,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("APP_ENV") == "production",
        path="/",
    )
    store.record_audit(
        user.id,
        "user.login",
        entity_table="users",
        entity_id=str(user.id),
        ip=client_ip_from_request(request),
    )
    return AuthTokenResponse(access_token=token, user=user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    store: Annotated[JobStore, Depends(get_store)],
    cache: Annotated[CacheBackend, Depends(get_cache)],
    ctx: Annotated[AuthContext | None, Depends(get_auth_context)] = None,
) -> None:
    """Revoke current cookie/session token and clear session cookie."""
    if ctx is not None and ctx.session_id is not None and ctx.session_exp is not None:
        cache.set_json(
            f"revoked_sess:{ctx.session_id}",
            True,
            ttl_s=max(1, ctx.session_exp - int(time.time())),
        )
        store.record_audit(
            ctx.user.id,
            "user.logout",
            entity_table="users",
            entity_id=str(ctx.user.id),
            ip=client_ip_from_request(request),
        )
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", httponly=True, samesite="lax")


@router.get("/me")
async def get_me(ctx: Annotated[AuthContext, Depends(require_auth)]) -> UserRead:
    """Return authenticated user profile."""
    return ctx.user


@router.post("/tokens", status_code=status.HTTP_201_CREATED)
async def create_api_token(
    payload: ApiTokenCreate,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> ApiTokenCreated:
    """Mint a scoped programmatic Bearer API token."""
    scopes = list(dict.fromkeys(s.strip() for s in payload.scopes if s.strip()))
    if not scopes:
        raise APIProblemError(
            422,
            "Invalid Scopes",
            "At least one non-empty scope is required.",
            error_type="https://spherex.example/problems/invalid-scopes",
        )
    raw, digest = generate_api_token()
    rec = store.create_api_token(ctx.user.id, payload.name, scopes, digest)
    store.record_audit(
        ctx.user.id,
        "api_token.create",
        entity_table="api_tokens",
        entity_id=str(rec.id),
        after={"name": rec.name, "scopes": rec.scopes},
        ip=client_ip_from_request(request),
    )
    data: dict[str, Any] = rec.model_dump()
    return ApiTokenCreated(**data, token=raw)


@router.get("/tokens")
async def list_api_tokens(
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_auth)],
) -> list[ApiTokenRead]:
    """List current user's API tokens."""
    return store.list_api_tokens(ctx.user.id)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(
    token_id: UUID,
    request: Request,
    store: Annotated[JobStore, Depends(get_store)],
    ctx: Annotated[AuthContext, Depends(require_write)],
) -> None:
    """Revoke an API token by id."""
    if not store.revoke_api_token(ctx.user.id, token_id):
        raise APIProblemError(
            status.HTTP_404_NOT_FOUND,
            "API Token Not Found",
            f"No active API token found for {token_id}.",
        )
    store.record_audit(
        ctx.user.id,
        "api_token.revoke",
        entity_table="api_tokens",
        entity_id=str(token_id),
        ip=client_ip_from_request(request),
    )
