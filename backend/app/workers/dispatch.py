"""Dispatch helpers used by LangGraph nodes (API process)."""

from __future__ import annotations

from typing import Any

from celery import chord, group

from app.core.config import get_settings
from app.workers import job_store
from app.workers.tasks.final_assembly import assemble_final
from app.workers.tasks.image import generate_image
from app.workers.tasks.music import generate_music
from app.workers.tasks.scene_render import render_scene
from app.workers.tasks.sfx import generate_sfx
from app.workers.tasks.tts import generate_tts
from app.workers.tasks.video import generate_video

TASK_MAP = {
    "image": generate_image,
    "video": generate_video,
    "tts": generate_tts,
    "sfx": generate_sfx,
    "music": generate_music,
    "scene_render": render_scene,
    "final_assembly": assemble_final,
}


def _db():
    settings = get_settings()
    db = job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)
    job_store.ensure_job_indexes(db)
    return db


def _needs_dispatch(job: dict[str, Any]) -> bool:
    return job.get("status") != "succeeded"


def _send(task, job_id: str) -> str | None:
    """Enqueue (or eagerly run) a task. Failures are recorded in Mongo; do not abort batch."""
    try:
        result = task.delay(job_id)
        return getattr(result, "id", None)
    except Exception:
        return None


def create_job(
    *,
    project_id: str,
    task_type: str,
    scene_id: str | None = None,
    shot_id: str | None = None,
    input_payload: dict[str, Any] | None = None,
    simulate_failure: bool = False,
) -> dict[str, Any]:
    db = _db()
    return job_store.create_or_get_job(
        db,
        project_id=project_id,
        task_type=task_type,
        scene_id=scene_id,
        shot_id=shot_id,
        input_payload=input_payload or {},
        simulate_failure=simulate_failure,
    )


def dispatch_jobs(jobs: list[dict[str, Any]]) -> list[str]:
    """Send Celery tasks for jobs that are not already succeeded (idempotent)."""
    db = _db()
    pending = [j for j in jobs if _needs_dispatch(j)]
    if not pending:
        return [j["id"] for j in jobs]

    settings = get_settings()
    if len(pending) > 1 and not settings.celery_task_always_eager:
        sigs = [TASK_MAP[job["type"]].s(job["id"]) for job in pending]
        async_result = group(sigs).apply_async()
        job_store.update_job(db, pending[0]["id"], celery_task_id=str(async_result.id))
    else:
        for job in pending:
            celery_id = _send(TASK_MAP[job["type"]], job["id"])
            if celery_id:
                job_store.update_job(db, job["id"], celery_task_id=celery_id)

    return [j["id"] for j in jobs]


def dispatch_image_jobs(
    project_id: str,
    scene_ids: list[str],
    *,
    regen_token: str | None = None,
) -> list[str]:
    jobs = [
        create_job(
            project_id=project_id,
            task_type="image",
            scene_id=scene_id,
            input_payload={
                "scene_id": scene_id,
                "kind": "storyboard",
                **({"regen_token": regen_token, "force": True} if regen_token else {}),
            },
        )
        for scene_id in scene_ids
    ]
    return dispatch_jobs(jobs)


def dispatch_shot_video_jobs(
    project_id: str,
    shots: list[dict[str, Any]],
    *,
    fail_shot_ids: set[str] | None = None,
    regen_token: str | None = None,
) -> list[str]:
    """Parallel shot video jobs only (scene composition runs at assembly)."""
    fail_shot_ids = fail_shot_ids or set()
    force = {"regen_token": regen_token, "force": True} if regen_token else {}
    shot_jobs = [
        create_job(
            project_id=project_id,
            task_type="video",
            scene_id=shot["scene_id"],
            shot_id=shot["id"],
            input_payload={
                "shot_id": shot["id"],
                "scene_id": shot["scene_id"],
                "attempt": shot.get("_attempt", 0),
                "duration_seconds": float(shot.get("duration_seconds") or 5),
                "prompt": str(shot.get("description") or shot.get("title") or shot["id"]),
                **force,
            },
            simulate_failure=shot["id"] in fail_shot_ids,
        )
        for shot in shots
    ]
    return dispatch_jobs(shot_jobs)


def dispatch_audio_jobs(
    project_id: str,
    scenes: list[dict[str, Any]],
    *,
    language: str | None = None,
    genre: str | None = None,
    regen_token: str | None = None,
) -> list[str]:
    """Per-scene TTS, SFX, and background music."""
    from app.workers.project_context import get_project_doc, spoken_text_for_scene

    project = get_project_doc(project_id) or {}
    lang = (language or str(project.get("language") or "Hindi")).strip() or "Hindi"
    force = {"regen_token": regen_token, "force": True} if regen_token else {}
    jobs: list[dict[str, Any]] = []
    for scene in scenes:
        scene_id = scene["id"] if isinstance(scene, dict) else str(scene)
        title = str(scene.get("title") or scene_id) if isinstance(scene, dict) else scene_id
        description = str(scene.get("description") or "") if isinstance(scene, dict) else ""
        duration = float(scene.get("duration_seconds") or 10) if isinstance(scene, dict) else 10.0
        # Spoken lines in selected language — never the English visual description.
        narration = spoken_text_for_scene(
            project_id,
            scene_id,
            scene=scene if isinstance(scene, dict) else None,
            language=lang,
        )
        base = {
            "scene_id": scene_id,
            "scene_title": title,
            "scene_description": description,
            "duration_seconds": duration,
            "language": lang,
            "genre": genre,
            "script": narration,
            **force,
        }
        jobs.append(
            create_job(
                project_id=project_id,
                task_type="tts",
                scene_id=scene_id,
                input_payload={**base, "text": narration},
            )
        )
        jobs.append(
            create_job(
                project_id=project_id,
                task_type="sfx",
                scene_id=scene_id,
                input_payload=base,
            )
        )
        jobs.append(
            create_job(
                project_id=project_id,
                task_type="music",
                scene_id=scene_id,
                input_payload=base,
            )
        )
    return dispatch_jobs(jobs)


def dispatch_assembly_jobs(
    project_id: str,
    *,
    scenes: list[dict[str, Any]] | None = None,
    regenerate_scene_ids: list[str] | None = None,
    retry_counts: dict[str, int] | None = None,
    regen_token: str | None = None,
) -> list[str]:
    """Scene FFmpeg compose (parallel), then final concat (chord callback)."""
    regen = set(regenerate_scene_ids or [])
    retry_counts = retry_counts or {}
    force = {"regen_token": regen_token, "force": True} if regen_token else {}
    all_scene_ids = [
        (s["id"] if isinstance(s, dict) else str(s))
        for s in (scenes or [])
    ]
    if regen:
        scene_ids_to_render = [sid for sid in all_scene_ids if sid in regen] or list(regen)
    else:
        scene_ids_to_render = all_scene_ids

    scene_jobs = [
        create_job(
            project_id=project_id,
            task_type="scene_render",
            scene_id=scene_id,
            input_payload={
                "scene_id": scene_id,
                "attempt": retry_counts.get(scene_id, 0),
                "duration_seconds": next(
                    (
                        float(s.get("duration_seconds") or 10)
                        for s in (scenes or [])
                        if (s.get("id") if isinstance(s, dict) else str(s)) == scene_id
                    ),
                    10.0,
                ),
                **force,
            },
        )
        for scene_id in scene_ids_to_render
    ]

    final_job = create_job(
        project_id=project_id,
        task_type="final_assembly",
        input_payload={
            "project_id": project_id,
            "scene_ids": sorted(all_scene_ids) if all_scene_ids else None,
            **force,
        },
    )

    settings = get_settings()
    db = _db()
    pending_scenes = [j for j in scene_jobs if _needs_dispatch(j)]
    pending_final = final_job if _needs_dispatch(final_job) else None

    if not pending_scenes:
        if pending_final:
            dispatch_jobs([final_job])
        return [j["id"] for j in scene_jobs] + [final_job["id"]]

    if not settings.celery_task_always_eager and pending_final:
        header = group(render_scene.s(j["id"]) for j in pending_scenes)
        body = assemble_final.si(final_job["id"])
        async_result = chord(header)(body)
        job_store.update_job(
            db,
            pending_scenes[0]["id"],
            celery_task_id=str(async_result.id),
        )
    else:
        dispatch_jobs(scene_jobs)
        if pending_final:
            dispatch_jobs([final_job])

    return [j["id"] for j in scene_jobs] + [final_job["id"]]
