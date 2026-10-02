"""Billing / provider credit endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.providers.fal.client import FalAPIError, FalClient
from app.schemas.auth import UserResponse
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/fal-credits")
async def fal_credits(
    _: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    settings = get_settings()
    key = (settings.fal_key or "").strip()
    if not key:
        return {
            "available": False,
            "balance": None,
            "currency": "USD",
            "username": None,
            "message": "FAL_KEY not configured",
        }
    try:
        with FalClient(key, timeout=20.0) as client:
            data = client.get_account_billing()
        credits = data.get("credits") or {}
        return {
            "available": True,
            "balance": credits.get("current_balance"),
            "currency": credits.get("currency") or "USD",
            "username": data.get("username"),
            "message": None,
        }
    except FalAPIError as exc:
        return {
            "available": False,
            "balance": None,
            "currency": "USD",
            "username": None,
            "message": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "balance": None,
            "currency": "USD",
            "username": None,
            "message": f"Could not load fal credits: {exc}",
        }
