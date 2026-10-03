"""Celery execution layer + job store tests (eager mode)."""

from __future__ import annotations

import hmac
import uuid

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.workers import job_store
from app.workers.dispatch import create_job, dispatch_jobs, dispatch_shot_video_jobs
from app.workers.redis_utils import celery_broker_use_ssl
from app.workers.tasks.video import generate_video


def _db():
    settings = get_settings()
    db = job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)
    job_store.ensure_job_indexes(db)
    return db


def test_redis_ssl_helper_for_rediss() -> None:
    assert celery_broker_use_ssl("redis://localhost:6379/0") is None
    ssl_opts = celery_broker_use_ssl("rediss://example:6380/0", cert_reqs="none")
    assert ssl_opts is not None
    assert "ssl_cert_reqs" in ssl_opts


def test_job_idempotency(orchestration_db) -> None:
    db = _db()
    project_id = f"p-{uuid.uuid4().hex[:8]}"
    first = job_store.create_or_get_job(
        db,
        project_id=project_id,
        task_type="video",
        scene_id="scene-1",
        shot_id="shot-1",
        input_payload={"shot_id": "shot-1", "attempt": 0},
    )
    second = job_store.create_or_get_job(
        db,
        project_id=project_id,
        task_type="video",
        scene_id="scene-1",
        shot_id="shot-1",
        input_payload={"shot_id": "shot-1", "attempt": 0},
    )
    assert first["id"] == second["id"]
    assert second.get("idempotent_hit") is True


def test_parallel_shot_dispatch_and_progress(orchestration_db) -> None:
    project_id = f"p-{uuid.uuid4().hex[:8]}"
    shots = [
        {"id": "scene-1-shot-1", "scene_id": "scene-1", "_attempt": 0},
        {"id": "scene-1-shot-2", "scene_id": "scene-1", "_attempt": 0},
    ]
    job_ids = dispatch_shot_video_jobs(project_id, shots)
    assert len(job_ids) >= 2
    db = _db()
    assert job_store.jobs_all_terminal(db, job_ids)
    jobs = [job_store.get_job(db, jid) for jid in job_ids]
    assert all(j and j["status"] in {"succeeded", "completed"} for j in jobs)
    assert all((j or {}).get("progress") == 100 for j in jobs)


def test_simulated_failure_and_retry_path(orchestration_db) -> None:
    db = _db()
    project_id = f"p-{uuid.uuid4().hex[:8]}"
    job = create_job(
        project_id=project_id,
        task_type="video",
        scene_id="scene-1",
        shot_id="shot-fail",
        input_payload={"shot_id": "shot-fail", "attempt": 0},
        simulate_failure=True,
    )
    with pytest.raises(Exception):
        generate_video.apply(args=(job["id"],)).get()
    failed = job_store.get_job(db, job["id"])
    assert failed is not None
    assert failed["status"] in {"failed", "timed_out", "retrying", "failed_retryable"}

    # New attempt hash → new job succeeds
    job2 = create_job(
        project_id=project_id,
        task_type="video",
        scene_id="scene-1",
        shot_id="shot-fail",
        input_payload={"shot_id": "shot-fail", "attempt": 1},
        simulate_failure=False,
    )
    assert job2["id"] != job["id"]
    result = generate_video.apply(args=(job2["id"],)).get()
    assert result["status"] in {"succeeded", "completed"}


def test_job_failure_persists_provider_error_fields(orchestration_db) -> None:
    from app.providers.base import ProviderError
    from app.workers import task_runtime

    db = _db()
    project_id = f"p-{uuid.uuid4().hex[:8]}"
    job = create_job(
        project_id=project_id,
        task_type="video",
        shot_id="shot-provider-err",
        input_payload={"shot_id": "shot-provider-err"},
    )

    def boom(_tmp):
        raise ProviderError(
            "All video providers failed: ['fal']",
            errors=["fal: content rejected"],
            error_class="CONTENT_REJECTED",
            fal_request_id="req-abc-123",
            fal_calls=3,
        )

    class FakeTask:
        request = type("R", (), {"id": "celery-1", "retries": 0})()

        def retry(self, exc=None, countdown=0):
            raise exc

    with pytest.raises(ProviderError):
        task_runtime.run_mock_job(FakeTask(), job_id=job["id"], work=boom)  # type: ignore[arg-type]

    saved = job_store.get_job(db, job["id"])
    assert saved is not None
    assert saved["status"] == "failed"
    assert saved["errors"] == ["fal: content rejected"]
    assert saved["error_class"] == "CONTENT_REJECTED"
    assert saved["fal_request_id"] == "req-abc-123"
    assert saved["fal_calls"] == 3


def test_timeout_marks_job(orchestration_db, monkeypatch) -> None:
    from celery.exceptions import SoftTimeLimitExceeded
    from app.workers import task_runtime

    db = _db()
    project_id = f"p-{uuid.uuid4().hex[:8]}"
    job = create_job(
        project_id=project_id,
        task_type="video",
        shot_id="shot-timeout",
        input_payload={"shot_id": "shot-timeout"},
    )

    def boom(_tmp):
        raise SoftTimeLimitExceeded()

    class FakeTask:
        request = type("R", (), {"id": "celery-1", "retries": 0})()

        def retry(self, exc=None, countdown=0):
            raise exc

    with pytest.raises(SoftTimeLimitExceeded):
        task_runtime.run_mock_job(FakeTask(), job_id=job["id"], work=boom)  # type: ignore[arg-type]

    saved = job_store.get_job(db, job["id"])
    assert saved is not None
    assert saved["status"] == "timed_out"


@pytest.mark.asyncio
async def test_internal_callback_auth(client: AsyncClient) -> None:
    bad = await client.post("/api/internal/jobs/missing/complete")
    assert bad.status_code == 401

    missing = await client.post(
        "/api/internal/jobs/missing/complete",
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_internal_callback_accepts_valid_token(
    client: AsyncClient,
    orchestration_db,
) -> None:
    db = _db()
    project_id = f"p-{uuid.uuid4().hex[:8]}"
    job = job_store.create_or_get_job(
        db,
        project_id=project_id,
        task_type="video",
        input_payload={"x": 1},
    )
    job_store.mark_job_succeeded(db, job["id"], {"output_key": "r2://x"})
    ok = await client.post(
        f"/api/internal/jobs/{job['id']}/complete",
        headers={"X-Internal-Token": "test-internal-token"},
        json={"source": "test"},
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    # constant-time compare sanity
    assert hmac.compare_digest(b"test-internal-token", b"test-internal-token")
