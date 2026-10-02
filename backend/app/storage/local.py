"""Local filesystem storage for development when R2 env is missing."""

from __future__ import annotations

import time
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

import httpx

from app.storage.base import StorageBackend


class LocalStorage(StorageBackend):
    def __init__(self, root: str | Path, *, public_base_url: str | None = None) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_base_url = (public_base_url or "").rstrip("/") or None

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root)):
            raise ValueError(f"Invalid storage key: {key}")
        return path

    def upload(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str = "application/octet-stream",
    ) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = data.read() if hasattr(data, "read") else data  # type: ignore[union-attr]
        path.write_bytes(payload)  # type: ignore[arg-type]
        _ = content_type
        return key

    def download(self, key: str) -> bytes:
        path = self._path(key)
        if path.is_file():
            return path.read_bytes()
        if self.public_base_url:
            url = f"{self.public_base_url}/{quote(key, safe='/')}"
            with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                response = client.get(url)
                if response.status_code >= 400:
                    raise FileNotFoundError(key)
                data = response.content
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data
        raise FileNotFoundError(key)

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()

    def exists(self, key: str) -> bool:
        if self._path(key).is_file():
            return True
        if not self.public_base_url:
            return False
        url = f"{self.public_base_url}/{quote(key, safe='/')}"
        try:
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                response = client.head(url)
                if response.status_code < 400:
                    return True
                response = client.get(url, headers={"Range": "bytes=0-0"})
                return response.status_code < 400
        except Exception:
            return False

    def presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        expires = int(time.time()) + max(1, expires_in)
        encoded = quote(key, safe="/")
        if self.public_base_url:
            if not self.exists(key):
                raise FileNotFoundError(key)
            return f"{self.public_base_url}/{encoded}?expires={expires}"
        if not self._path(key).is_file():
            raise FileNotFoundError(key)
        return f"file://{self._path(key)}?expires={expires}"
