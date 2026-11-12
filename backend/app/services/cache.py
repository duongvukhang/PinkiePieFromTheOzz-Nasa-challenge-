"""Redis hot-cache wrapper with thread-safe in-memory TTL fallback."""

from __future__ import annotations

import json
import os
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import redis


class CacheBackend(ABC):
    """Abstract JSON key-value cache with TTL expiration."""

    @abstractmethod
    def get_json(self, key: str) -> Any | None:
        """Return cached JSON-decoded value for `key`, or None if missing/expired."""

    @abstractmethod
    def set_json(self, key: str, value: Any, *, ttl_s: int = 3600) -> None:
        """Store JSON-serializable `value` at `key` for `ttl_s` seconds."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove `key` from cache if present."""


class MemoryCacheBackend(CacheBackend):
    """Thread-safe in-process TTL cache for tests and offline fallback."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float | None, str]] = {}

    def get_json(self, key: str) -> Any | None:
        now = self._clock()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, raw = entry
            if expires_at is not None and now >= expires_at:
                del self._store[key]
                return None
            return json.loads(raw)

    def set_json(self, key: str, value: Any, *, ttl_s: int = 3600) -> None:
        expires_at = self._clock() + ttl_s if ttl_s > 0 else None
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True)
        with self._lock:
            self._store[key] = (expires_at, raw)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)


class RedisCacheBackend(CacheBackend):
    """Redis JSON cache that degrades gracefully on transient Redis errors."""

    def __init__(self, url: str, *, client: redis.Redis | None = None) -> None:
        self._client = client or redis.Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=1.0, socket_timeout=1.0
        )

    def get_json(self, key: str) -> Any | None:
        try:
            raw = self._client.get(key)
        except redis.RedisError:
            return None
        if raw is None:
            return None
        return json.loads(str(raw))

    def set_json(self, key: str, value: Any, *, ttl_s: int = 3600) -> None:
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True)
        try:
            if ttl_s > 0:
                self._client.setex(key, ttl_s, raw)
            else:
                self._client.set(key, raw)
        except redis.RedisError:
            return None

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except redis.RedisError:
            return None


@lru_cache(maxsize=1)
def get_cache_backend() -> CacheBackend:
    """Return configured Redis cache backend, or MemoryCacheBackend if disabled/unset."""
    mode = os.environ.get("SPHX_CACHE_BACKEND", "").lower()
    redis_url = os.environ.get("REDIS_URL")
    if mode != "memory" and redis_url:
        return RedisCacheBackend(redis_url)
    return MemoryCacheBackend()
