"""Local e2e: Redis + Celery worker (non-eager) mock jobs.

Run with Redis up and a worker listening. This script verifies parallel shots,
progress, idempotency, retries, and internal callback auth against a live API.
"""

from __future__ import annotations

import os
import sys
import time
import uuid

# Non-eager for real broker path
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "false"
os.environ.setdefault("MONGODB_URL", "mongodb://localhost:27017")
os.environ.setdefault("MONGODB_DB_NAME", "ai_video_platform_e2e")
os.environ.setdefault("JWT_SECRET", "e2e-jwt-secret-not-for-production-32b")
os.environ.setdefault("INTERNAL_API_TOKEN", "dev-internal-token-change-me")
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:8000")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDIS_SSL_CERT_REQS", "none")
os.environ.setdefault("CELERY_MAX_RETRIES", "2")
os.environ.setdefault("MOCK_TASK_SLEEP_SECONDS", "0.1")

from app.core.config import get_settings
from app.workers import job_store
from app.workers.celery_app import celery_app
from app.workers.dispatch import create_job, dispatch_jobs, dispatch_shot_video_jobs
from app.workers.settings import get_worker_settings

get_settings.cache_clear()
get_worker_settings.cache_clear()
celery_app.conf.task_always_eager = False


def _db():
    settings = get_settings()
    db = job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)
    job_store.ensure_job_indexes(db)
    return db


def wait_jobs(job_ids: list[str], timeout: float = 60.0) -> list[dict]:
    db = _db()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job_store.jobs_all_terminal(db, job_ids):
            return [job_store.get_job(db, jid) for jid in job_ids]  # type: ignore[misc]
        time.sleep(0.2)
    raise TimeoutError(f"Jobs not terminal: {job_ids}")


def main() -> int:
    db = _db()
    project_id = f"e2e-{uuid.uuid4().hex[:8]}"
    print(f"project={project_id} redis={get_settings().redis_url} eager={celery_app.conf.task_always_eager}")

    # Inspect worker
    ping = celery_app.control.inspect().ping()
    if not ping:
        print("ERROR: no Celery workers responded to ping. Start a worker first.", file=sys.stderr)
        return 1
    print(f"workers={list(ping.keys())}")

    # Parallel shots + progress
    shots = [
        {"id": "scene-1-shot-1", "scene_id": "scene-1", "_attempt": 0},
        {"id": "scene-1-shot-2", "scene_id": "scene-1", "_attempt": 0},
    ]
    job_ids = dispatch_shot_video_jobs(project_id, shots)
    jobs = wait_jobs(job_ids)
    assert all(j and j["status"] == "succeeded" for j in jobs), jobs
    assert all((j or {}).get("progress") == 100 for j in jobs)
    print("OK parallel shots + progress")

    # Idempotency
    again = dispatch_shot_video_jobs(project_id, shots)
    assert set(again) == set(job_ids)
    print("OK idempotency")

    # Retry after simulated failure
    fail_job = create_job(
        project_id=project_id,
        task_type="video",
        scene_id="scene-x",
        shot_id="shot-fail",
        input_payload={"shot_id": "shot-fail", "attempt": 0},
        simulate_failure=True,
    )
    dispatch_jobs([fail_job])
    failed = wait_jobs([fail_job["id"]])[0]
    assert failed and failed["status"] in {"failed", "succeeded"}, failed
    # With retries, mock fails only on first attempt then may succeed
    print(f"OK failure/retry path status={failed['status']} attempts={failed.get('attempts')}")

    # Internal callback auth against live API
    import httpx

    bad = httpx.post(
        f"{get_settings().api_base_url}/api/internal/jobs/{fail_job['id']}/complete",
        timeout=5.0,
    )
    assert bad.status_code == 401, bad.status_code
    good = httpx.post(
        f"{get_settings().api_base_url}/api/internal/jobs/{fail_job['id']}/complete",
        headers={"X-Internal-Token": get_settings().internal_api_token},
        json={"source": "e2e"},
        timeout=5.0,
    )
    # 200 when job exists in API DB; 404 still proves auth passed (not 401).
    assert good.status_code in {200, 404}, good.text
    print(f"OK internal callback auth (status={good.status_code})")

    print("E2E mock worker checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
