"""MongoDB assets collection — metadata only, never bytes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pymongo.database import Database

from app.workers.job_store import get_db


def assets_collection(db: Database):
    return db["assets"]


def ensure_asset_indexes(db: Database) -> None:
    col = assets_collection(db)
    col.create_index("project_id")
    col.create_index("r2_key", unique=True)
    col.create_index([("project_id", 1), ("type", 1)])
    col.create_index("job_id")


def serialize_asset(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    result = dict(doc)
    if "_id" in result:
        result["id"] = str(result.pop("_id"))
    return result


def create_asset(
    db: Database,
    *,
    project_id: str,
    asset_type: str,
    r2_key: str,
    mime: str,
    size: int,
    provider: str,
    cost: float,
    scene_id: str | None = None,
    shot_id: str | None = None,
    duration: float | None = None,
    job_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_asset_indexes(db)
    now = datetime.now(UTC)
    col = assets_collection(db)
    existing = col.find_one({"r2_key": r2_key})
    fields = {
        "project_id": project_id,
        "scene_id": scene_id,
        "shot_id": shot_id,
        "type": asset_type,
        "r2_key": r2_key,
        "mime": mime,
        "size": size,
        "duration": duration,
        "provider": provider,
        "cost": float(cost),
        "job_id": job_id,
        "metadata": metadata or {},
        "created_at": now,
    }
    if existing is not None:
        col.update_one({"_id": existing["_id"]}, {"$set": fields})
        existing.update(fields)
        return serialize_asset(existing)  # type: ignore[return-value]

    asset_id = str(uuid.uuid4())
    doc = {"_id": asset_id, **fields}
    col.insert_one(doc)
    return serialize_asset(doc)  # type: ignore[return-value]


def get_asset(db: Database, asset_id: str) -> dict[str, Any] | None:
    return serialize_asset(assets_collection(db).find_one({"_id": asset_id}))


def list_assets_for_project(db: Database, project_id: str) -> list[dict[str, Any]]:
    cursor = assets_collection(db).find({"project_id": project_id}).sort("created_at", 1)
    return [serialize_asset(doc) for doc in cursor if doc]  # type: ignore[misc]


def sum_cost_for_job(db: Database, job_id: str) -> float:
    pipeline = [
        {"$match": {"job_id": job_id}},
        {"$group": {"_id": None, "total": {"$sum": "$cost"}}},
    ]
    rows = list(assets_collection(db).aggregate(pipeline))
    if not rows:
        return 0.0
    return float(rows[0].get("total") or 0.0)


def sum_cost_for_project(db: Database, project_id: str) -> float:
    pipeline = [
        {"$match": {"project_id": project_id}},
        {"$group": {"_id": None, "total": {"$sum": "$cost"}}},
    ]
    rows = list(assets_collection(db).aggregate(pipeline))
    if not rows:
        return 0.0
    return float(rows[0].get("total") or 0.0)


def get_sync_db_from_settings() -> Database:
    try:
        from app.core.config import get_settings

        settings = get_settings()
        return get_db(settings.mongodb_url, settings.mongodb_db_name)
    except Exception:
        from app.workers.settings import get_worker_settings

        settings = get_worker_settings()
        return get_db(settings.mongodb_url, settings.mongodb_db_name)
