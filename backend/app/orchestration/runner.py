from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException, status
from langgraph.types import Command
from pymongo.asynchronous.database import AsyncDatabase

from app.orchestration.checkpointing import thread_config
from app.orchestration.graph import compile_graph
from app.orchestration.state import empty_workflow_state
from app.orchestration.status import production_status_payload
from app.schemas.auth import UserResponse
from app.schemas.project import ProjectUpdate
from app.services import project_service

_running_tasks: dict[str, asyncio.Task[Any]] = {}


def _get_graph():
    return compile_graph()


def _invoke_graph(input_data: Any, config: dict[str, Any]) -> Any:
    graph = _get_graph()
    return graph.invoke(input_data, config)


async def _run_in_background(project_id: str, input_data: Any) -> None:
    config = thread_config(project_id)
    try:
        await asyncio.to_thread(_invoke_graph, input_data, config)
    finally:
        _running_tasks.pop(project_id, None)


def _cancel_running(project_id: str) -> None:
    """Stop in-process production task so an explicit user retry can start."""
    existing = _running_tasks.pop(project_id, None)
    if existing is not None and not existing.done():
        existing.cancel()


def _schedule(project_id: str, input_data: Any) -> None:
    existing = _running_tasks.get(project_id)
    if existing and not existing.done():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Production already running for this project",
        )
    task = asyncio.create_task(_run_in_background(project_id, input_data))
    _running_tasks[project_id] = task


def _is_running(project_id: str) -> bool:
    task = _running_tasks.get(project_id)
    return bool(task and not task.done())


async def start_production(
    db: AsyncDatabase,
    user: UserResponse,
    project_id: str,
    *,
    fail_scene_ids: list[str] | None = None,
    max_retries: int = 2,
) -> dict[str, Any]:
    await project_service.get_project(db, user, project_id)

    if _is_running(project_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Production already running for this project",
        )

    graph = _get_graph()
    config = thread_config(project_id)
    snapshot = await asyncio.to_thread(graph.get_state, config)
    if snapshot.values and snapshot.values.get("status") == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Production already completed for this project",
        )
    if snapshot.values and snapshot.next and not snapshot.interrupts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Production already in progress",
        )

    initial = empty_workflow_state(project_id, max_retries=max_retries)
    if fail_scene_ids:
        initial["fail_scene_ids"] = list(fail_scene_ids)

    await project_service.update_project(
        db,
        user,
        project_id,
        ProjectUpdate(status="producing"),
    )

    _schedule(project_id, initial)
    return {
        "project_id": project_id,
        "status": "accepted",
        "message": "Production graph started in background",
    }


async def get_production_status(project_id: str) -> dict[str, Any]:
    # Fallback reconcile: if awaiting jobs that are already terminal, resume.
    try:
        from app.api.internal import maybe_resume_project_from_jobs

        await maybe_resume_project_from_jobs(project_id)
    except Exception:
        pass

    graph = _get_graph()
    config = thread_config(project_id)
    snapshot = await asyncio.to_thread(graph.get_state, config)
    if not snapshot.values:
        from app.orchestration.status import collect_project_issues

        return {
            "projectId": project_id,
            "status": "pending",
            "currentStep": None,
            "pauseReason": None,
            "steps": [],
            "scenes": [],
            "shots": [],
            "sceneStatus": {},
            "shotStatus": {},
            "retryCounts": {},
            "errors": [],
            "issues": collect_project_issues(project_id),
            "maxRetries": 2,
            "running": _is_running(project_id),
            "interrupted": False,
        }

    payload = production_status_payload(snapshot.values)
    payload["running"] = _is_running(project_id)
    payload["interrupted"] = bool(snapshot.interrupts)
    # Mongo project status wins when final assembly already marked completed.
    try:
        from app.workers.project_context import get_project_doc

        project = get_project_doc(project_id) or {}
        if str(project.get("status") or "") == "completed":
            payload["status"] = "completed"
            payload["running"] = False
            payload["issues"] = []
            for step in payload.get("steps") or []:
                step["status"] = "completed"
            for scene in payload.get("scenes") or []:
                scene["status"] = "completed"
            for shot in payload.get("shots") or []:
                shot["status"] = "completed"
    except Exception:
        pass
    return payload


async def resume_production(project_id: str) -> dict[str, Any]:
    if _is_running(project_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Production already running for this project",
        )

    graph = _get_graph()
    config = thread_config(project_id)
    snapshot = await asyncio.to_thread(graph.get_state, config)
    if not snapshot.values:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No production state found for this project",
        )
    if snapshot.values.get("status") == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Production already completed",
        )
    if not snapshot.interrupts and not snapshot.next:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nothing to resume",
        )

    _schedule(project_id, Command(resume=True))
    return {
        "project_id": project_id,
        "status": "accepted",
        "message": "Production resume scheduled",
    }


async def regenerate_production(
    project_id: str,
    *,
    scene_ids: list[str] | None = None,
    shot_ids: list[str] | None = None,
    include_stills: bool = False,
    motion_mode: str = "auto",
    prompt_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    import uuid

    scene_ids = list(scene_ids or [])
    shot_ids = list(shot_ids or [])
    if not scene_ids and not shot_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide scene_ids and/or shot_ids",
        )
    # Explicit user retry wins over a stuck/in-flight run.
    _cancel_running(project_id)

    graph = _get_graph()
    config = thread_config(project_id)
    snapshot = await asyncio.to_thread(graph.get_state, config)
    if not snapshot.values:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No production state found for this project",
        )

    values = dict(snapshot.values)
    regen_token = str(uuid.uuid4())

    # Parent scenes of regenerating shots must be pending for assembly.
    parent_scenes = {
        str(shot.get("scene_id"))
        for shot in values.get("shots") or []
        if shot.get("id") in shot_ids and shot.get("scene_id")
    }
    scenes = []
    for scene in values.get("scenes") or []:
        item = dict(scene)
        if item["id"] in scene_ids or item["id"] in parent_scenes:
            item["status"] = "pending"
        scenes.append(item)

    shots = []
    for shot in values.get("shots") or []:
        item = dict(shot)
        if item["id"] in shot_ids or item["scene_id"] in scene_ids:
            item["status"] = "pending"
        shots.append(item)

    scene_status = dict(values.get("scene_status") or {})
    shot_status = dict(values.get("shot_status") or {})
    image_ready = set(values.get("image_ready_shot_ids") or [])
    for sid in set(scene_ids) | parent_scenes:
        scene_status[sid] = "pending"
    target_shot_ids = set(shot_ids)
    for shot in shots:
        if shot["id"] in shot_ids or shot["scene_id"] in scene_ids:
            shot_status[shot["id"]] = "pending"
            target_shot_ids.add(shot["id"])
            if include_stills or shot["scene_id"] in scene_ids:
                image_ready.discard(shot["id"])

    # Start story cursor on the earliest targeted scene.
    target_scenes = sorted(
        {
            str(s.get("scene_id") or s.get("id"))
            for s in (values.get("shots") or [])
            if s.get("id") in target_shot_ids
        }
        | set(scene_ids)
        | parent_scenes,
        key=lambda sid: next(
            (
                int(sc.get("order") or 0)
                for sc in (values.get("scenes") or [])
                if str(sc.get("id")) == sid
            ),
            0,
        ),
    )
    active = target_scenes[0] if target_scenes else ""

    update = {
        "scenes": scenes,
        "shots": shots,
        "scene_status": scene_status,
        "shot_status": shot_status,
        "regenerate_scene_ids": scene_ids,
        "regenerate_shot_ids": shot_ids,
        "video_prompt_overrides": dict(prompt_overrides or {}),
        "video_motion_mode": motion_mode or "auto",
        "active_scene_id": active,
        "image_ready_shot_ids": sorted(image_ready),
        "regen_token": regen_token,
        "status": "running",
        "pause_reason": "",
        "current_step": "video_generation_planning",
    }

    # Scene / new_still → re-enter before asset_planning (Flux still + Wan clip).
    # Clip-only → as_node=asset_planning so next node is video_generation_planning.
    if scene_ids or include_stills:
        as_node = "shot_planning"
    else:
        as_node = "asset_planning"

    def _apply_and_continue() -> None:
        try:
            graph.update_state(
                config,
                update,
                as_node=as_node,
            )
            graph.invoke(None, config)
        finally:
            _running_tasks.pop(project_id, None)

    task = asyncio.create_task(asyncio.to_thread(_apply_and_continue))
    _running_tasks[project_id] = task

    return {
        "project_id": project_id,
        "status": "accepted",
        "message": "Partial regeneration scheduled",
        "scene_ids": scene_ids,
        "shot_ids": shot_ids,
    }


async def fix_shot_production(
    project_id: str,
    *,
    shot_id: str,
    mode: str = "auto_fix",
    guidance: str | None = None,
) -> dict[str, Any]:
    """User-friendly fix: auto safe motion, guided notes, retry, or new still."""
    from app.providers.fal.prompt_fix import rewrite_motion_prompt

    mode_norm = (mode or "auto_fix").strip().lower()
    if mode_norm not in {"auto_fix", "guided", "retry", "new_still"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="mode must be auto_fix, guided, retry, or new_still",
        )

    overrides: dict[str, str] = {}
    motion_mode = "auto"
    include_stills = False
    message = "Clip retry scheduled with safe motion prompt"

    if mode_norm == "new_still":
        include_stills = True
        motion_mode = "ultra"
        message = "New still + clip scheduled"
    elif mode_norm == "auto_fix":
        motion_mode = "ultra"
        overrides[shot_id] = rewrite_motion_prompt(None, mode="ultra")
        message = "AI safe-motion clip retry scheduled (no story/weapons in prompt)"
    elif mode_norm == "guided":
        notes = (guidance or "").strip()
        if not notes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="guidance is required for guided mode (camera/mood notes)",
            )
        motion_mode = "guided"
        overrides[shot_id] = rewrite_motion_prompt(notes, mode="guided")
        message = "Clip retry scheduled with your camera/mood notes"
    else:  # retry
        motion_mode = "auto"
        message = "Clip retry scheduled (motion-only prompt)"

    result = await regenerate_production(
        project_id,
        shot_ids=[shot_id],
        include_stills=include_stills,
        motion_mode=motion_mode,
        prompt_overrides=overrides,
    )
    return {
        **result,
        "mode": mode_norm,
        "message": message,
        "motionPrompt": overrides.get(shot_id),
    }


async def wait_until_idle(project_id: str, timeout: float = 10.0) -> None:
    """Test helper: wait for background task to finish."""
    deadline = asyncio.get_event_loop().time() + timeout
    while _is_running(project_id):
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"Production still running for {project_id}")
        await asyncio.sleep(0.05)
