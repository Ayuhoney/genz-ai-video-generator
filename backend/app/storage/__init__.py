"""Storage factory and R2 key helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.storage.base import StorageBackend
from app.storage.local import LocalStorage
from app.storage.r2 import R2Storage


def build_r2_key(
    *,
    project_id: str,
    asset_type: str,
    filename: str,
    scene_id: str | None = None,
    shot_id: str | None = None,
) -> str:
    """projects/{project_id}/scenes/{scene_id}/shots/{shot_id}/{type}/{filename}"""
    scene = scene_id or "_"
    shot = shot_id or "_"
    safe_name = filename.lstrip("/")
    return (
        f"projects/{project_id}/scenes/{scene}/shots/{shot}/"
        f"{asset_type}/{safe_name}"
    )


def r2_configured(
    *,
    account_id: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
    bucket: str | None,
) -> bool:
    return bool(account_id and access_key_id and secret_access_key and bucket)


def create_storage(
    *,
    account_id: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    bucket: str | None = None,
    endpoint_url: str | None = None,
    public_base_url: str | None = None,
    media_local_root: str = "./media",
) -> StorageBackend:
    if r2_configured(
        account_id=account_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket=bucket,
    ):
        return R2Storage(
            bucket=bucket or "",
            account_id=account_id,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            endpoint_url=endpoint_url,
            public_base_url=public_base_url,
        )
    root = Path(media_local_root)
    return LocalStorage(root, public_base_url=public_base_url)


@lru_cache
def get_storage() -> StorageBackend:
    """Process-wide storage backend from env (API or worker settings)."""
    try:
        from app.core.config import get_settings

        settings = get_settings()
        return create_storage(
            account_id=settings.r2_account_id,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket=settings.r2_bucket,
            endpoint_url=settings.r2_endpoint_url,
            public_base_url=settings.r2_public_base_url,
            media_local_root=settings.media_local_root,
        )
    except Exception:
        from app.workers.settings import get_worker_settings

        settings = get_worker_settings()
        return create_storage(
            account_id=settings.r2_account_id,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket=settings.r2_bucket,
            endpoint_url=settings.r2_endpoint_url,
            public_base_url=settings.r2_public_base_url,
            media_local_root=settings.media_local_root,
        )


def reset_storage_cache() -> None:
    get_storage.cache_clear()
