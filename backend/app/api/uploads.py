"""Upload character reference images for face lock."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.schemas.auth import UserResponse
from app.services.auth_service import get_current_user

router = APIRouter(tags=["uploads"])

ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_BYTES = 8 * 1024 * 1024


def _refs_root() -> Path:
    settings = get_settings()
    root = Path(settings.media_local_root).resolve() / "character-refs"
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.post("/api/uploads/character-ref")
async def upload_character_ref(
    file: UploadFile = File(...),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, str | bool]:
    content_type = (file.content_type or "").lower().strip()
    ext = ALLOWED_TYPES.get(content_type)
    if not ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPEG, PNG, or WebP images are allowed.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file.",
        )
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image must be under 8MB.",
        )

    settings = get_settings()
    file_id = uuid.uuid4().hex
    rel_key = f"{current_user.id}/{file_id}{ext}"
    dest = _refs_root() / rel_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)

    public_base = (settings.api_base_url or "http://127.0.0.1:8000").rstrip("/")
    url = f"{public_base}/api/media/character-refs/{rel_key}"
    return {
        "url": url,
        "key": f"character-refs/{rel_key}",
        "faceLocked": True,
    }


@router.get("/api/media/character-refs/{user_id}/{filename}")
async def get_character_ref(user_id: str, filename: str) -> FileResponse:
    if ".." in user_id or ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid path")
    path = _refs_root() / user_id / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    suffix = path.suffix.lower()
    media = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    return FileResponse(path, media_type=media)
