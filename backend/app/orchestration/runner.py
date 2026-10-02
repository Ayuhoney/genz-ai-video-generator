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
            "maxRetries": 2,
            "running": _is_running(project_id),
            "interrupted": False,
        }

    payload = production_status_payload(snapshot.values)
    payload["running"] = _is_running(project_id)
    payload["interrupted"] = bool(snapshot.interrupts)
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
) -> dict[str, Any]:
    import uuid

    scene_ids = list(scene_ids or [])
    shot_ids = list(shot_ids or [])
    if not scene_ids and not shot_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide scene_ids and/or shot_ids",
        )
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
    for sid in set(scene_ids) | parent_scenes:
        scene_status[sid] = "pending"
    for shot in shots:
        if shot["id"] in shot_ids or shot["scene_id"] in scene_ids:
            shot_status[shot["id"]] = "pending"

    update = {
        "scenes": scenes,
        "shots": shots,
        "scene_status": scene_status,
        "shot_status": shot_status,
        "regenerate_scene_ids": scene_ids,
        "regenerate_shot_ids": shot_ids,
        "regen_token": regen_token,
        "status": "running",
        "pause_reason": "",
        "current_step": "video_generation_planning",
    }

    # Scene regen redoes stills; shot-only skips asset_planning via empty scene list.
    as_node = "shot_planning" if scene_ids else "asset_planning"

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


async def wait_until_idle(project_id: str, timeout: float = 10.0) -> None:
    """Test helper: wait for background task to finish."""
    deadline = asyncio.get_event_loop().time() + timeout
    while _is_running(project_id):
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"Production still running for {project_id}")
        await asyncio.sleep(0.05)
