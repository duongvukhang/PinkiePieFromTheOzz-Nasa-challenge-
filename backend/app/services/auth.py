"""Password hashing, HMAC session tokens, API token helpers, and scope checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from fastapi import Request

from app.schemas.auth import UserRead

SESSION_COOKIE_NAME = "sphx_session"
SESSION_TTL_S = 7 * 86400
SESSION_PREFIX = "sphx_sess_"
TOKEN_PREFIX = "sphx_tok_"
_WRITE_SCOPES = frozenset({"write", "admin", "*", "projects:write"})
_EPHEMERAL_SECRET = secrets.token_bytes(32)
_PH = PasswordHasher()


@dataclass(frozen=True)
class AuthContext:
    """Resolved authenticated caller identity, scopes, and session/token provenance."""

    user: UserRead
    scopes: frozenset[str]
    session_id: str | None = None
    session_exp: int | None = None
    token_id: UUID | None = None


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    return _PH.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Verify a plaintext password against a stored Argon2 hash."""
    if not password_hash:
        return False
    try:
        return bool(_PH.verify(password_hash, password))
    except Argon2Error:
        return False


def hash_token(raw: str) -> bytes:
    """Return 32-byte SHA-256 digest for token/share-secret storage."""
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _session_secret() -> bytes:
    raw = os.environ.get("SPHX_SESSION_SECRET") or os.environ.get("SECRET_KEY")
    return raw.encode("utf-8") if raw else _EPHEMERAL_SECRET


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def mint_session_token(user_id: UUID, ttl_s: int = SESSION_TTL_S) -> str:
    """Mint an HMAC-SHA256 signed session token valid for `ttl_s` seconds."""
    exp = int(time.time()) + ttl_s
    payload = json.dumps(
        {"uid": str(user_id), "sid": uuid4().hex, "exp": exp},
        separators=(",", ":"),
    ).encode("utf-8")
    body = _b64e(payload)
    sig = _b64e(hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{SESSION_PREFIX}{body}.{sig}"


def verify_session_token(token: str) -> tuple[UUID, str, int] | None:
    """Validate HMAC signature and expiration, returning `(user_id, sid, exp)` or None."""
    if not token.startswith(SESSION_PREFIX):
        return None
    rest = token[len(SESSION_PREFIX) :]
    body, sep, sig = rest.partition(".")
    if not sep or not body or not sig:
        return None
    expected = _b64e(hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_b64d(body))
        exp = int(data["exp"])
        if exp <= int(time.time()):
            return None
        return UUID(str(data["uid"])), str(data["sid"]), exp
    except Exception:
        return None


def generate_api_token() -> tuple[str, bytes]:
    """Generate a scoped API Bearer token and its SHA-256 digest."""
    raw = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    return raw, hash_token(raw)


def generate_share_token() -> tuple[str, bytes]:
    """Generate a revocable project share token and its SHA-256 digest."""
    raw = f"sphx_shr_{secrets.token_urlsafe(24)}"
    return raw, hash_token(raw)


def has_write_scope(scopes: frozenset[str]) -> bool:
    """Return True if the caller's scopes permit mutating operations."""
    return bool(scopes & _WRITE_SCOPES)


def client_ip_from_request(request: Request) -> str | None:
    """Extract caller IP string for audit logging, or None if unavailable."""
    return request.client.host if request.client else None
