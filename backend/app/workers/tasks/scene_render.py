from __future__ import annotations

from pathlib import Path
from typing import Any

from celery import chord, group

from app.workers import job_store
from app.workers.celery_app import celery_app
from app.workers.ffmpeg_render import render_scene_for_job
from app.workers.settings import get_worker_settings
from app.workers.task_runtime import JobTask, run_mock_job
from app.workers.tasks.video import generate_video


@celery_app.task(bind=True, base=JobTask, name="workers.scene_render.render")
def render_scene(self: JobTask, job_id: str) -> dict[str, Any]:
    def work(_tmp: Path) -> dict[str, Any]:
        db = job_store.get_db(
            get_worker_settings().mongodb_url,
            get_worker_settings().mongodb_db_name,
        )
        job = job_store.get_job(db, job_id)
        if job is None:
            raise ValueError(f"Unknown job_id={job_id}")
        return render_scene_for_job(job)

    return run_mock_job(self, job_id=job_id, work=work)


@celery_app.task(name="workers.scene_render.shots_complete")
def shots_complete(results: list[dict[str, Any]], scene_job_id: str) -> dict[str, Any]:
    """Chord callback after parallel shot video jobs finish."""
    return {
        "scene_job_id": scene_job_id,
        "shot_results": results,
        "ok": all(r.get("status") in {"succeeded", "completed"} for r in results),
    }


def dispatch_parallel_shot_videos(shot_job_ids: list[str], scene_job_id: str):
    """Run shot video jobs in parallel, then a scene-level callback."""
    header = group(generate_video.s(job_id) for job_id in shot_job_ids)
    return chord(header)(shots_complete.s(scene_job_id))
