from __future__ import annotations

from pathlib import Path
from typing import Any

from app.workers.celery_app import celery_app
from app.workers.produce import produce_image
from app.workers.task_runtime import JobTask, run_mock_job
from app.workers import job_store
from app.workers.settings import get_worker_settings


@celery_app.task(bind=True, base=JobTask, name="workers.image.generate")
def generate_image(self: JobTask, job_id: str) -> dict[str, Any]:
    def work(_tmp: Path) -> dict[str, Any]:
        db = job_store.get_db(
            get_worker_settings().mongodb_url,
            get_worker_settings().mongodb_db_name,
        )
        job = job_store.get_job(db, job_id)
        if job is None:
            raise ValueError(f"Unknown job_id={job_id}")
        return produce_image(job)

    return run_mock_job(self, job_id=job_id, work=work)
