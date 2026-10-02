"""Object storage interface (R2 or local media)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO


class StorageBackend(ABC):
    """Upload/download/delete + time-limited signed URLs. Never persists to MongoDB."""

    @abstractmethod
    def upload(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Store object; return the storage key."""

    @abstractmethod
    def download(self, key: str) -> bytes:
        """Return object bytes."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete object if present."""

    @abstractmethod
    def presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        """Private time-limited URL for the object."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if the key exists."""
