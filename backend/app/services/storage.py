"""Object and local filesystem storage backends with SHA-256 integrity checks."""

from __future__ import annotations

import hashlib
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


class StorageError(RuntimeError):
    """Base error for FITS storage operations."""


class ChecksumMismatchError(StorageError):
    """Raised when stored or downloaded bytes fail SHA-256 verification."""


@dataclass(frozen=True)
class StorageObject:
    """Stored object descriptor (key, SHA-256 hex digest, size in bytes)."""

    key: str
    sha256: str
    size_bytes: int


def compute_sha256(data: bytes) -> str:
    """Return lowercase SHA-256 hex digest for raw payload bytes."""
    return hashlib.sha256(data).hexdigest()


def _normalize_key(key: str) -> str:
    clean = key.lstrip("/")
    if not clean or "\x00" in clean or any(p in ("", ".", "..") for p in Path(clean).parts):
        raise ValueError(f"Invalid or unsafe storage key: {key!r}")
    return clean


def _verify_sha256(key: str, data: bytes, expected: str | None) -> None:
    if expected is not None:
        actual = compute_sha256(data)
        if actual != expected:
            raise ChecksumMismatchError(
                f"Checksum mismatch for {key}: expected {expected}, got {actual}"
            )


class StorageBackend(ABC):
    """Abstract binary object store for cached cutouts and FITS files."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if an object exists at `key`."""

    @abstractmethod
    def get_bytes(self, key: str, *, verify_checksum: bool = True) -> bytes:
        """Read object bytes at `key`, optionally verifying stored SHA-256."""

    @abstractmethod
    def get_sha256(self, key: str) -> str | None:
        """Return stored SHA-256 hex digest for `key` if recorded."""

    @abstractmethod
    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/fits",
        metadata: dict[str, str] | None = None,
    ) -> StorageObject:
        """Persist `data` atomically at `key` with SHA-256 checksum metadata."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete object at `key` if present."""


class LocalStorageBackend(StorageBackend):
    """Atomic local-disk object store with `.sha256` sidecar verification."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _path_for(self, key: str) -> Path:
        target = (self.root / _normalize_key(key)).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError(f"Key escapes storage root: {key!r}")
        return target

    @staticmethod
    def _sidecar(path: Path) -> Path:
        return path.with_name(f"{path.name}.sha256")

    def exists(self, key: str) -> bool:
        return self._path_for(key).is_file()

    def get_sha256(self, key: str) -> str | None:
        sidecar = self._sidecar(self._path_for(key))
        return sidecar.read_text(encoding="utf-8").strip() if sidecar.is_file() else None

    def get_bytes(self, key: str, *, verify_checksum: bool = True) -> bytes:
        path = self._path_for(key)
        if not path.is_file():
            raise FileNotFoundError(f"Storage key not found: {key}")
        data = path.read_bytes()
        if verify_checksum:
            _verify_sha256(key, data, self.get_sha256(key))
        return data

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/fits",
        metadata: dict[str, str] | None = None,
    ) -> StorageObject:
        del content_type, metadata
        rel = _normalize_key(key)
        path = self._path_for(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        sha256 = compute_sha256(data)
        self._atomic_write(path, data)
        self._atomic_write(self._sidecar(path), f"{sha256}\n".encode())
        return StorageObject(key=rel, sha256=sha256, size_bytes=len(data))

    def delete(self, key: str) -> None:
        path = self._path_for(key)
        path.unlink(missing_ok=True)
        self._sidecar(path).unlink(missing_ok=True)

    @staticmethod
    def _atomic_write(target: Path, data: bytes) -> None:
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_name, target)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise


class S3StorageBackend(StorageBackend):
    """S3/MinIO object store recording `sha256` in object metadata."""

    _NOT_FOUND = {"404", "NoSuchKey", "NotFound"}

    def __init__(
        self,
        bucket: str,
        *,
        client: Any | None = None,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region_name: str | None = None,
    ) -> None:
        self.bucket = bucket
        self._client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region_name or "us-east-1",
        )

    def _head(self, key: str) -> dict[str, Any] | None:
        rel = _normalize_key(key)
        try:
            res: dict[str, Any] = self._client.head_object(Bucket=self.bucket, Key=rel)
            return res
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code", "")) in self._NOT_FOUND:
                return None
            raise StorageError(f"S3 head_object failed for {rel}: {exc}") from exc

    def exists(self, key: str) -> bool:
        return self._head(key) is not None

    def get_sha256(self, key: str) -> str | None:
        head = self._head(key)
        val = head.get("Metadata", {}).get("sha256") if head else None
        return str(val) if val is not None else None

    def get_bytes(self, key: str, *, verify_checksum: bool = True) -> bytes:
        rel = _normalize_key(key)
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=rel)
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code", "")) in self._NOT_FOUND:
                raise FileNotFoundError(f"S3 key not found: {rel}") from exc
            raise StorageError(f"S3 get_object failed for {rel}: {exc}") from exc
        data = bytes(resp["Body"].read())
        if verify_checksum:
            _verify_sha256(rel, data, resp.get("Metadata", {}).get("sha256"))
        return data

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/fits",
        metadata: dict[str, str] | None = None,
    ) -> StorageObject:
        rel = _normalize_key(key)
        sha256 = compute_sha256(data)
        self._client.put_object(
            Bucket=self.bucket,
            Key=rel,
            Body=data,
            ContentType=content_type,
            Metadata={**(metadata or {}), "sha256": sha256},
        )
        return StorageObject(key=rel, sha256=sha256, size_bytes=len(data))

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=_normalize_key(key))


def get_storage_backend() -> StorageBackend:
    """Create configured storage backend from environment variables."""
    mode = os.environ.get("SPHX_STORAGE_BACKEND", "").lower()
    s3_endpoint = os.environ.get("S3_ENDPOINT")
    if mode == "s3" or (mode != "local" and s3_endpoint):
        return S3StorageBackend(
            bucket=os.environ.get("S3_BUCKET", "sphx"),
            endpoint_url=s3_endpoint,
            access_key=os.environ.get("S3_ACCESS_KEY"),
            secret_key=os.environ.get("S3_SECRET_KEY"),
        )
    return LocalStorageBackend(Path(os.environ.get("SPHX_STORAGE_DIR", ".cache/sphx_store")))
