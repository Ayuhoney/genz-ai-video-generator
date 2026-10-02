"""MongoDB job store used by API and Celery workers (sync pymongo)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

JobStatus = Literal[
    "queued",
    "running",
    "retrying",
    "succeeded",
    "failed",
    "timed_out",
]

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "timed_out"})

_client: MongoClient | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def get_sync_client(mongodb_url: str) -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(mongodb_url)
    return _client


def close_sync_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def get_db(mongodb_url: str, db_name: str) -> Database:
    return get_sync_client(mongodb_url)[db_name]


def jobs_collection(db: Database) -> Collection:
    return db["jobs"]


def ensure_job_indexes(db: Database) -> None:
    col = jobs_collection(db)
    col.create_index("idempotency_key", unique=True)
    col.create_index([("project_id", ASCENDING), ("created_at", ASCENDING)])
    col.create_index("status")


def make_idempotency_key(
    *,
    project_id: str,
    scene_id: str | None,
    shot_id: str | None,
    task_type: str,
    input_hash: str,
) -> str:
    raw = "|".join(
        [
            project_id,
            scene_id or "",
            shot_id or "",
            task_type,
            input_hash,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_inputs(payload: dict[str, Any]) -> str:
    encoded = repr(sorted(payload.items())).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_or_get_job(
    db: Database,
    *,
    project_id: str,
    task_type: str,
    scene_id: str | None = None,
    shot_id: str | None = None,
    input_payload: dict[str, Any] | None = None,
    simulate_failure: bool = False,
) -> dict[str, Any]:
    ensure_job_indexes(db)
    payload = input_payload or {}
    input_hash = hash_inputs(payload)
    idempotency_key = make_idempotency_key(
        project_id=project_id,
        scene_id=scene_id,
        shot_id=shot_id,
        task_type=task_type,
        input_hash=input_hash,
    )
    col = jobs_collection(db)
    existing = col.find_one({"idempotency_key": idempotency_key})
    if existing is not None:
        existing["id"] = str(existing.pop("_id"))
        existing["idempotent_hit"] = True
        return existing

    job_id = str(uuid.uuid4())
    now = _utcnow()
    doc = {
        "_id": job_id,
        "idempotency_key": idempotency_key,
        "status": "queued",
        "progress": 0,
        "attempts": 0,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
        "project_id": project_id,
        "scene_id": scene_id,
        "shot_id": shot_id,
        "type": task_type,
        "input_hash": input_hash,
        "input": payload,
        "output": None,
        "celery_task_id": None,
        "simulate_failure": simulate_failure,
    }
    try:
        col.insert_one(doc)
    except Exception:
        existing = col.find_one({"idempotency_key": idempotency_key})
        if existing is None:
            raise
        existing["id"] = str(existing.pop("_id"))
        existing["idempotent_hit"] = True
        return existing

    doc["id"] = job_id
    del doc["_id"]
    doc["idempotent_hit"] = False
    return doc


def serialize_job(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    result = dict(doc)
    if "_id" in result:
        result["id"] = str(result.pop("_id"))
    return result


def get_job(db: Database, job_id: str) -> dict[str, Any] | None:
    return serialize_job(jobs_collection(db).find_one({"_id": job_id}))


def list_jobs_for_project(db: Database, project_id: str) -> list[dict[str, Any]]:
    cursor = jobs_collection(db).find({"project_id": project_id}).sort("created_at", 1)
    return [serialize_job(doc) for doc in cursor if doc]  # type: ignore[misc]


def update_job(
    db: Database,
    job_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    fields["updated_at"] = _utcnow()
    doc = jobs_collection(db).find_one_and_update(
        {"_id": job_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return serialize_job(doc)


def mark_job_running(db: Database, job_id: str, *, celery_task_id: str | None = None) -> dict[str, Any] | None:
    fields: dict[str, Any] = {
        "status": "running",
        "started_at": _utcnow(),
        "progress": max(1, 0),
    }
    if celery_task_id:
        fields["celery_task_id"] = celery_task_id
    # increment attempts
    jobs_collection(db).update_one({"_id": job_id}, {"$inc": {"attempts": 1}})
    return update_job(db, job_id, **fields)


def mark_job_progress(db: Database, job_id: str, progress: int) -> dict[str, Any] | None:
    return update_job(db, job_id, progress=max(0, min(100, progress)), status="running")


def mark_job_retrying(db: Database, job_id: str, error: str) -> dict[str, Any] | None:
    return update_job(db, job_id, status="retrying", error=error)


def mark_job_succeeded(
    db: Database,
    job_id: str,
    output: dict[str, Any],
) -> dict[str, Any] | None:
    return update_job(
        db,
        job_id,
        status="succeeded",
        progress=100,
        output=output,
        error=None,
        finished_at=_utcnow(),
    )


def mark_job_failed(
    db: Database,
    job_id: str,
    error: str,
    *,
    timed_out: bool = False,
) -> dict[str, Any] | None:
    return update_job(
        db,
        job_id,
        status="timed_out" if timed_out else "failed",
        error=error,
        finished_at=_utcnow(),
    )


def jobs_all_terminal(db: Database, job_ids: list[str]) -> bool:
    if not job_ids:
        return True
    col = jobs_collection(db)
    count = col.count_documents(
        {"_id": {"$in": job_ids}, "status": {"$in": list(TERMINAL_STATUSES)}}
    )
    return count == len(job_ids)


def jobs_any_failed(db: Database, job_ids: list[str]) -> bool:
    if not job_ids:
        return False
    return (
        jobs_collection(db).count_documents(
            {
                "_id": {"$in": job_ids},
                "status": {"$in": ["failed", "timed_out"]},
            }
        )
        > 0
    )
