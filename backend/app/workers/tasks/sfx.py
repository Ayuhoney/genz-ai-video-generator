from __future__ import annotations

from pathlib import Path
from typing import Any

from app.workers import job_store
from app.workers.celery_app import celery_app
from app.workers.produce import produce_sfx
from app.workers.settings import get_worker_settings
from app.workers.task_runtime import JobTask, run_mock_job


@celery_app.task(
    bind=True,
    base=JobTask,
    name="workers.sfx.generate",
    # Fail fast: hung fal mmaudio must not pin worker slots for 15+ minutes.
    soft_time_limit=210,
    time_limit=240,
)
def generate_sfx(self: JobTask, job_id: str) -> dict[str, Any]:
    def work(_tmp: Path) -> dict[str, Any]:
        db = job_store.get_db(
            get_worker_settings().mongodb_url,
            get_worker_settings().mongodb_db_name,
        )
        job = job_store.get_job(db, job_id)
        if job is None:
            raise ValueError(f"Unknown job_id={job_id}")
        return produce_sfx(job)

    return run_mock_job(self, job_id=job_id, work=work)
