"""Per-job budget, circuit breaker, and fal call cost accounting."""

from __future__ import annotations

import logging
from typing import Any

from app.providers.base import BudgetExceededError
from app.providers.fal.errors import FalClipError, FalErrorClass, FalErrorInfo
from app.storage.asset_store import get_sync_db_from_settings, sum_cost_for_project

logger = logging.getLogger(__name__)

CIRCUIT_BREAKER_LIMIT = 5
_IDEMPOTENCY_TTL = 24 * 60 * 60
_IDEMPOTENCY_PREFIX = "fal:clip:idemp:"


def _settings() -> Any:
    try:
        from app.workers.settings import get_worker_settings

        return get_worker_settings()
    except Exception:
        from app.core.config import get_settings

        return get_settings()


def job_max_usd() -> float:
    s = _settings()
    return float(getattr(s, "job_max_usd", 0) or 0)


def project_max_usd() -> float:
    s = _settings()
    return float(getattr(s, "max_project_cost_usd", 0) or 0)


def estimated_job_spend(db: Any, project_id: str) -> float:
    """Sum recorded asset costs + reserved estimates on open video jobs."""
    spent = float(sum_cost_for_project(db, project_id) or 0.0)
    try:
        from app.workers import job_store

        jobs = job_store.list_jobs_for_project(db, project_id)
        reserved = 0.0
        for j in jobs:
            if j.get("type") != "video":
                continue
            if j.get("status") in {
                "completed",
                "succeeded",
                "failed",
                "timed_out",
                "needs_new_prompt",
                "needs_new_image",
                "failed_retryable",
            }:
                continue
            reserved += float(j.get("estimated_cost_usd") or 0.0)
        return spent + reserved
    except Exception:
        return spent


def enforce_job_budget(project_id: str, *, next_estimate: float = 0.0) -> None:
    """Pause project when JOB_MAX_USD or MAX_PROJECT_COST_USD would be exceeded."""
    db = get_sync_db_from_settings()
    limits = [v for v in (job_max_usd(), project_max_usd()) if v > 0]
    if not limits:
        return
    limit = min(limits)
    spent = estimated_job_spend(db, project_id)
    if spent + max(0.0, next_estimate) >= limit:
        from bson import ObjectId

        filt: list[dict[str, Any]] = [{"_id": project_id}]
        if ObjectId.is_valid(project_id):
            filt.append({"_id": ObjectId(project_id)})
        db.projects.update_one(
            {"$or": filt},
            {"$set": {"status": "paused_budget"}},
        )
        logger.error(
            "budget pause project_id=%s spent=%.4f next=%.4f limit=%.4f",
            project_id,
            spent,
            next_estimate,
            limit,
        )
        raise BudgetExceededError(project_id, spent, limit)


def record_clip_cost_log(
    *,
    project_id: str,
    clip_id: str | None,
    model_id: str,
    estimated: float,
    fal_calls: int,
) -> None:
    db = get_sync_db_from_settings()
    total = estimated_job_spend(db, project_id)
    logger.info(
        "fal cost clip_id=%s project_id=%s model=%s estimated_usd=%.4f "
        "fal_calls=%d project_estimated_total_usd=%.4f",
        clip_id,
        project_id,
        model_id,
        estimated,
        fal_calls,
        total,
    )


def _project_filter(project_id: str) -> dict[str, Any]:
    from bson import ObjectId

    filt: list[dict[str, Any]] = [{"_id": project_id}]
    if ObjectId.is_valid(project_id):
        filt.append({"_id": ObjectId(project_id)})
    return {"$or": filt}


def bump_circuit_breaker(project_id: str, *, failed: bool) -> None:
    """5 consecutive clip failures → pause project for review."""
    db = get_sync_db_from_settings()
    filt = _project_filter(project_id)
    if not failed:
        db.projects.update_one(filt, {"$set": {"fal_consecutive_clip_failures": 0}})
        return
    from pymongo import ReturnDocument

    doc = db.projects.find_one_and_update(
        filt,
        {"$inc": {"fal_consecutive_clip_failures": 1}},
        return_document=ReturnDocument.AFTER,
    )
    count = int((doc or {}).get("fal_consecutive_clip_failures") or 0)
    if count >= CIRCUIT_BREAKER_LIMIT:
        db.projects.update_one(
            filt,
            {
                "$set": {
                    "status": "paused_review",
                    "pause_reason": (
                        f"circuit_breaker:{count}_consecutive_clip_failures"
                    ),
                }
            },
        )
        logger.error(
            "circuit breaker open project_id=%s failures=%d",
            project_id,
            count,
        )
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.FATAL,
                message=(
                    "Generation paused for review after repeated clip failures. "
                    "Our team has been notified."
                ),
            ),
            clip_status="failed",
        )


def ensure_project_not_paused(project_id: str) -> None:
    db = get_sync_db_from_settings()
    doc = db.projects.find_one(_project_filter(project_id))
    if not doc:
        return
    status = str(doc.get("status") or "")
    if status in {"paused_budget", "paused_review"}:
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.FATAL,
                message=(
                    "This project is paused. Please adjust content or contact support."
                ),
            ),
            clip_status="failed",
        )


def idempotency_claim(clip_id: str) -> str | None:
    """SET NX claim. Returns existing request token if already submitted."""
    if not clip_id:
        return None
    try:
        from app.workers.redis_utils import get_redis_client

        r = get_redis_client()
        key = f"{_IDEMPOTENCY_PREFIX}{clip_id}"
        existing = r.get(key)
        if existing:
            return str(existing)
        # Placeholder until real request_id known
        ok = r.set(key, "pending", nx=True, ex=_IDEMPOTENCY_TTL)
        if not ok:
            return str(r.get(key) or "pending")
        return None
    except Exception as exc:
        logger.debug("idempotency claim failed: %s", exc)
        return None


def idempotency_bind(clip_id: str, request_id: str) -> None:
    if not clip_id or not request_id:
        return
    try:
        from app.workers.redis_utils import get_redis_client

        get_redis_client().setex(
            f"{_IDEMPOTENCY_PREFIX}{clip_id}",
            _IDEMPOTENCY_TTL,
            request_id,
        )
    except Exception as exc:
        logger.debug("idempotency bind failed: %s", exc)


def idempotency_clear(clip_id: str) -> None:
    if not clip_id:
        return
    try:
        from app.workers.redis_utils import get_redis_client

        get_redis_client().delete(f"{_IDEMPOTENCY_PREFIX}{clip_id}")
    except Exception:
        pass
