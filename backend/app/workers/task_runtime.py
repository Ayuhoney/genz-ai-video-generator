"""Shared mock task execution helpers (worker-safe)."""

from __future__ import annotations

import random
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded

from app.providers.base import BudgetExceededError
from app.providers.fal.client import FalAPIError, is_content_blocked_error
from app.workers import job_store
from app.workers.settings import get_worker_settings


def _db():
    settings = get_worker_settings()
    return job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)


def notify_api_job_complete(job_id: str) -> None:
    settings = get_worker_settings()
    url = f"{settings.api_base_url.rstrip('/')}/api/internal/jobs/{job_id}/complete"
    try:
        httpx.post(
            url,
            headers={"X-Internal-Token": settings.internal_api_token},
            json={"source": "celery_worker"},
            timeout=10.0,
        )
    except Exception:
        # Callback is best-effort; API can reconcile from MongoDB.
        pass


def exponential_backoff_with_jitter(retries: int, base: float = 0.5, cap: float = 8.0) -> float:
    delay = min(cap, base * (2**retries))
    return delay * (0.5 + random.random())


class JobTask(Task):
    """Base Celery task with Mongo job tracking + manual retries."""

    # Retries are handled explicitly in run_mock_job (backoff + jitter).
    autoretry_for: tuple = ()
    max_retries = 3

    def retry_countdown(self) -> float:
        return exponential_backoff_with_jitter(self.request.retries)


def run_mock_job(
    self: JobTask,
    *,
    job_id: str,
    work: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    settings = get_worker_settings()
    db = _db()
    job = job_store.get_job(db, job_id)
    if job is None:
        raise ValueError(f"Unknown job_id={job_id}")

    # Idempotent success short-circuit
    if job.get("status") == "succeeded" and job.get("output"):
        return {"job_id": job_id, "status": "succeeded", "output": job["output"], "idempotent": True}

    job_store.mark_job_running(db, job_id, celery_task_id=self.request.id)
    job_store.mark_job_progress(db, job_id, 10)

    tmp_root = Path(tempfile.mkdtemp(prefix=f"job-{job_id[:8]}-"))
    try:
        if job.get("simulate_failure") and job.get("attempts", 1) <= 1:
            job_store.mark_job_progress(db, job_id, 40)
            time.sleep(settings.mock_task_sleep_seconds)
            raise RuntimeError(f"Simulated failure for job {job_id}")

        job_store.mark_job_progress(db, job_id, 40)
        time.sleep(settings.mock_task_sleep_seconds)
        job_store.mark_job_progress(db, job_id, 75)
        output = work(tmp_root)
        # Never rely on shared disk — return fake object keys only.
        output.setdefault("tmp_cleaned", True)
        job_store.mark_job_progress(db, job_id, 95)
        time.sleep(settings.mock_task_sleep_seconds)
        saved = job_store.mark_job_succeeded(db, job_id, output) or {}
        notify_api_job_complete(job_id)
        return {"job_id": job_id, "status": "succeeded", "output": saved.get("output")}
    except SoftTimeLimitExceeded as exc:
        job_store.mark_job_failed(db, job_id, "soft time limit exceeded", timed_out=True)
        notify_api_job_complete(job_id)
        raise exc
    except BudgetExceededError as exc:
        job_store.mark_job_failed(db, job_id, str(exc))
        notify_api_job_complete(job_id)
        raise
    except Exception as exc:
        # Content-policy / non-retryable provider errors: fail once (save fal credits).
        non_retryable = isinstance(exc, FalAPIError) and (
            getattr(exc, "retryable", True) is False or is_content_blocked_error(exc)
        )
        if non_retryable or is_content_blocked_error(exc):
            job_store.mark_job_failed(db, job_id, str(exc))
            notify_api_job_complete(job_id)
            raise
        retries = self.request.retries
        max_retries = get_worker_settings().celery_max_retries
        if retries < max_retries:
            job_store.mark_job_retrying(db, job_id, str(exc))
            raise self.retry(exc=exc, countdown=self.retry_countdown())
        job_store.mark_job_failed(db, job_id, str(exc))
        notify_api_job_complete(job_id)
        raise
    finally:
        # Clean temp dirs — workers must not leave shared disk artifacts.
        try:
            for path in sorted(tmp_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            tmp_root.rmdir()
        except Exception:
            pass
