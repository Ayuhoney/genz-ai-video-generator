"""Asset metadata + signed URL endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import get_settings
from app.db.mongodb import get_db
from app.schemas.auth import UserResponse
from app.services import project_service
from app.services.auth_service import get_current_user
from app.storage import get_storage, reset_storage_cache
from app.storage.asset_store import get_asset, list_assets_for_project
from app.workers.job_store import get_db as get_sync_db

router = APIRouter(prefix="/api/projects", tags=["assets"])


def _sync_db():
    settings = get_settings()
    return get_sync_db(settings.mongodb_url, settings.mongodb_db_name)


@router.get("/{project_id}/assets")
async def list_project_assets(
    project_id: str,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    await project_service.get_project(db, current_user, project_id)
    assets = list_assets_for_project(_sync_db(), project_id)
    return {"projectId": project_id, "assets": assets}


@router.get("/{project_id}/final-video/url")
async def get_final_video_signed_url(
    project_id: str,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    project = await project_service.get_project(db, current_user, project_id)
    asset_id = project.final_video_asset_id
    if not asset_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Final video not ready",
        )
    return await get_asset_signed_url(project_id, asset_id, db, current_user)


@router.get("/{project_id}/assets/{asset_id}/url")
async def get_asset_signed_url(
    project_id: str,
    asset_id: str,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    await project_service.get_project(db, current_user, project_id)
    asset = get_asset(_sync_db(), asset_id)
    if asset is None or asset.get("project_id") != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    settings = get_settings()
    storage = get_storage()
    try:
        url = storage.presigned_url(
            asset["r2_key"],
            expires_in=settings.presigned_url_expire_seconds,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset object missing from storage",
        ) from exc

    return {
        "projectId": project_id,
        "assetId": asset_id,
        "url": url,
        "expiresIn": settings.presigned_url_expire_seconds,
        "r2Key": asset["r2_key"],
        "mime": asset.get("mime"),
    }


# Re-export for tests that clear caches
__all__ = ["router", "reset_storage_cache"]
