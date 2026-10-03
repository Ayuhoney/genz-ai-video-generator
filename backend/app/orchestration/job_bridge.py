"""Reconcile Celery job state into LangGraph workflow state."""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.orchestration.state import WorkflowError, WorkflowState
from app.workers import job_store


def _db():
    settings = get_settings()
    return job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)


def jobs_terminal(job_ids: list[str]) -> bool:
    return job_store.jobs_all_terminal(_db(), job_ids)


def reconcile_pending_jobs(state: WorkflowState, *, step: str) -> WorkflowState:
    job_ids = list(state.get("pending_job_ids") or [])
    if not job_ids:
        return {
            "awaiting_jobs": False,
            "status": "running",
            "pause_reason": "",
            "current_step": step,  # type: ignore[typeddict-item]
        }

    db = _db()
    if not job_store.jobs_all_terminal(db, job_ids):
        return {
            "awaiting_jobs": True,
            "status": "paused",
            "pause_reason": "awaiting_jobs",
            "current_step": step,  # type: ignore[typeddict-item]
            "pending_job_ids": job_ids,
        }

    jobs = [job_store.get_job(db, jid) for jid in job_ids]
    jobs = [j for j in jobs if j is not None]
    failed = [
        j
        for j in jobs
        if j.get("status")
        in {
            "failed",
            "timed_out",
            "needs_new_image",
            "needs_new_prompt",
            "failed_retryable",
        }
    ]
    succeeded = [
        j for j in jobs if j.get("status") in {"succeeded", "completed"}
    ]

    scenes = [dict(s) for s in state.get("scenes") or []]
    shots = [dict(s) for s in state.get("shots") or []]
    scene_status = dict(state.get("scene_status") or {})
    shot_status = dict(state.get("shot_status") or {})
    retry_counts = dict(state.get("retry_counts") or {})
    errors = list(state.get("errors") or [])
    max_retries = int(state.get("max_retries") or 2)
    image_ready = set(state.get("image_ready_shot_ids") or [])

    for job in succeeded:
        shot_id = job.get("shot_id")
        scene_id = job.get("scene_id")
        job_type = str(job.get("type") or "")
        # Per-shot stills mark image-ready only — video clips complete the shot.
        if shot_id and job_type == "image":
            image_ready.add(str(shot_id))
        if shot_id and job_type == "video":
            shot_status[shot_id] = "completed"
            image_ready.add(str(shot_id))
            for shot in shots:
                if shot["id"] == shot_id:
                    shot["status"] = "completed"
        if scene_id and job_type in {"scene_render", "tts", "sfx", "music"}:
            related_shots = [s for s in shots if s["scene_id"] == scene_id]
            if related_shots and all(
                shot_status.get(s["id"]) == "completed" for s in related_shots
            ):
                scene_status[scene_id] = "completed"
                for scene in scenes:
                    if scene["id"] == scene_id:
                        scene["status"] = "completed"

    for scene in scenes:
        sid = scene["id"]
        related_shots = [s for s in shots if s["scene_id"] == sid]
        if not related_shots:
            continue
        if all(shot_status.get(s["id"]) == "completed" for s in related_shots):
            if scene_status.get(sid) != "failed" and scene.get("status") != "failed":
                scene_status[sid] = "completed"
                scene["status"] = "completed"

    if not failed:
        # asset_planning only prepares stills — leave scenes pending for video.
        if step in {"audio_planning", "assembly_planning"}:
            for scene in scenes:
                if scene["status"] != "failed":
                    scene["status"] = "completed"
                    scene_status[scene["id"]] = "completed"
        payload: dict[str, Any] = {
            "scenes": scenes,  # type: ignore[typeddict-item]
            "shots": shots,  # type: ignore[typeddict-item]
            "scene_status": scene_status,
            "shot_status": shot_status,
            "retry_counts": retry_counts,
            "errors": errors,
            "image_ready_shot_ids": sorted(image_ready),
            "pending_job_ids": [],
            "awaiting_jobs": False,
            "status": "running",
            "pause_reason": "",
            "current_step": step,  # type: ignore[typeddict-item]
        }
        # Keep regen targets through the story loop; clear only after audio/assembly.
        if step in {"audio_planning", "assembly_planning"}:
            payload["regenerate_scene_ids"] = []
            payload["regenerate_shot_ids"] = []
        return payload  # type: ignore[return-value]

    # Handle failures — pause for resume/retry without wiping completed work.
    failed_scene: str | None = None
    for job in failed:
        item_id = job.get("shot_id") or job.get("scene_id") or job["id"]
        err: WorkflowError = {
            "step": step,
            "message": job.get("error") or f"Job {job['id']} failed",
            "item_id": str(item_id),
        }
        errors.append(err)
        if job.get("shot_id"):
            shot_status[job["shot_id"]] = "failed"
            for shot in shots:
                if shot["id"] == job["shot_id"]:
                    shot["status"] = "failed"
                    failed_scene = shot["scene_id"]
        if job.get("scene_id"):
            failed_scene = job["scene_id"]
            scene_status[job["scene_id"]] = "failed"
            for scene in scenes:
                if scene["id"] == job["scene_id"]:
                    scene["status"] = "failed"

    if failed_scene:
        retry_counts[failed_scene] = retry_counts.get(failed_scene, 0) + 1
        if retry_counts[failed_scene] <= max_retries:
            status = "paused"
            pause_reason = f"Failed {failed_scene}; awaiting resume/retry"
        else:
            status = "failed"
            pause_reason = f"Exceeded retries for {failed_scene}"
    else:
        status = "paused"
        pause_reason = "Job failures; awaiting resume/retry"

    return {
        "scenes": scenes,  # type: ignore[typeddict-item]
        "shots": shots,  # type: ignore[typeddict-item]
        "scene_status": scene_status,
        "shot_status": shot_status,
        "retry_counts": retry_counts,
        "errors": errors,
        "image_ready_shot_ids": sorted(image_ready),
        "pending_job_ids": job_ids,
        "awaiting_jobs": False,
        "status": status,  # type: ignore[typeddict-item]
        "pause_reason": pause_reason,
        "current_step": step,  # type: ignore[typeddict-item]
    }


def maybe_await_or_reconcile(
    state: WorkflowState,
    *,
    step: str,
    job_ids: list[str],
) -> WorkflowState:
    """After dispatch: if jobs already terminal (eager), reconcile; else pause."""
    base: WorkflowState = {
        "pending_job_ids": job_ids,
        "awaiting_jobs": True,
        "current_step": step,  # type: ignore[typeddict-item]
    }
    if jobs_terminal(job_ids):
        merged = {**state, **base}
        return reconcile_pending_jobs(merged, step=step)
    return {
        **base,
        "status": "paused",
        "pause_reason": "awaiting_jobs",
    }
