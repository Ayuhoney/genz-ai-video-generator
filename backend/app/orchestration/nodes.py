from __future__ import annotations

from typing import Any

from app.orchestration.state import (
    ItemStatus,
    SceneState,
    ShotState,
    WorkflowError,
    WorkflowState,
)


def mock_director_plan(project_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "title": f"Director plan for {project_id}",
        "beats": ["setup", "conflict", "resolution"],
    }


def mock_scenes(
    *,
    regenerate_scene_ids: list[str] | None = None,
    existing: list[SceneState] | None = None,
) -> list[SceneState]:
    if regenerate_scene_ids and existing:
        scenes: list[SceneState] = []
        regenerate = set(regenerate_scene_ids)
        for scene in existing:
            if scene["id"] in regenerate:
                scenes.append({**scene, "status": "pending"})
            else:
                scenes.append(scene)
        return scenes

    return [
        {
            "id": "scene-1",
            "order": 1,
            "title": "Opening",
            "description": "Establish the world.",
            "duration_seconds": 12,
            "status": "pending",
        },
        {
            "id": "scene-2",
            "order": 2,
            "title": "Rising Action",
            "description": "Conflict appears.",
            "duration_seconds": 15,
            "status": "pending",
        },
        {
            "id": "scene-3",
            "order": 3,
            "title": "Climax",
            "description": "Decision moment.",
            "duration_seconds": 18,
            "status": "pending",
        },
    ]


def mock_shots(
    scenes: list[SceneState],
    *,
    regenerate_scene_ids: list[str] | None = None,
    regenerate_shot_ids: list[str] | None = None,
    existing: list[ShotState] | None = None,
) -> list[ShotState]:
    regenerate_scenes = set(regenerate_scene_ids or [])
    regenerate_shots = set(regenerate_shot_ids or [])

    if existing and (regenerate_scenes or regenerate_shots):
        shots: list[ShotState] = []
        for shot in existing:
            if shot["id"] in regenerate_shots or shot["scene_id"] in regenerate_scenes:
                shots.append({**shot, "status": "pending"})
            else:
                shots.append(shot)
        return shots

    shots = []
    from app.providers.fal.clip_timing import (
        clip_seconds_from_settings,
        plan_shot_durations,
        shot_beat_description,
    )

    try:
        from app.core.config import get_settings

        clip = clip_seconds_from_settings(get_settings())
    except Exception:
        clip = 5
    for scene in scenes:
        scene_dur = int(round(float(scene.get("duration_seconds") or clip)))
        durs = plan_shot_durations(scene_dur, max_clip=clip)
        base = soft_desc(scene)
        for index, dur in enumerate(durs, start=1):
            shots.append(
                {
                    "id": f"{scene['id']}-shot-{index}",
                    "scene_id": scene["id"],
                    "order": index,
                    "title": f"{scene['title']} / Shot {index}",
                    "description": shot_beat_description(base, index, len(durs)),
                    "status": "pending",
                    "duration_seconds": float(dur),
                }
            )
    return shots


def soft_desc(scene: dict[str, Any]) -> str:
    return str(scene.get("description") or scene.get("title") or scene["id"])


def _status_map(
    items: list[dict[str, Any]],
    key: str = "id",
) -> dict[str, ItemStatus]:
    return {str(item[key]): item["status"] for item in items}


def _append_error(
    errors: list[WorkflowError] | None,
    step: str,
    message: str,
    item_id: str | None = None,
) -> list[WorkflowError]:
    next_errors = list(errors or [])
    entry: WorkflowError = {"step": step, "message": message}
    if item_id:
        entry["item_id"] = item_id
    next_errors.append(entry)
    return next_errors


def _should_process_scene(state: WorkflowState, scene_id: str) -> bool:
    regenerate_scenes = set(state.get("regenerate_scene_ids") or [])
    regenerate_shots = set(state.get("regenerate_shot_ids") or [])
    if regenerate_scenes or regenerate_shots:
        if scene_id in regenerate_scenes:
            return True
        for shot in state.get("shots") or []:
            if shot.get("scene_id") == scene_id and shot.get("id") in regenerate_shots:
                return True
        return False
    status = (state.get("scene_status") or {}).get(scene_id)
    return status != "completed"


def _should_process_shot(state: WorkflowState, shot: ShotState) -> bool:
    regenerate_scenes = set(state.get("regenerate_scene_ids") or [])
    regenerate_shots = set(state.get("regenerate_shot_ids") or [])
    if regenerate_scenes or regenerate_shots:
        return shot["id"] in regenerate_shots or shot["scene_id"] in regenerate_scenes
    status = (state.get("shot_status") or {}).get(shot["id"])
    return status != "completed"


def process_scene_items(
    state: WorkflowState,
    *,
    step: str,
) -> WorkflowState:
    """Advance scene/shot statuses with optional simulated failures."""
    scenes = [dict(scene) for scene in state.get("scenes") or []]
    shots = [dict(shot) for shot in state.get("shots") or []]
    scene_status = dict(state.get("scene_status") or {})
    shot_status = dict(state.get("shot_status") or {})
    retry_counts = dict(state.get("retry_counts") or {})
    errors = list(state.get("errors") or [])
    fail_ids = set(state.get("fail_scene_ids") or [])
    max_retries = int(state.get("max_retries") or 2)

    failed_item: str | None = None

    for scene in scenes:
        scene_id = scene["id"]
        if not _should_process_scene(state, scene_id):
            continue

        scene["status"] = "running"
        scene_status[scene_id] = "running"

        should_fail = scene_id in fail_ids and retry_counts.get(scene_id, 0) < 1
        if should_fail:
            retry_counts[scene_id] = retry_counts.get(scene_id, 0) + 1
            scene["status"] = "failed"
            scene_status[scene_id] = "failed"
            for shot in shots:
                if shot["scene_id"] == scene_id and _should_process_shot(state, shot):
                    shot["status"] = "failed"
                    shot_status[shot["id"]] = "failed"
            errors = _append_error(
                errors,
                step,
                f"Simulated failure for {scene_id}",
                item_id=scene_id,
            )
            failed_item = scene_id
            break

        scene["status"] = "completed"
        scene_status[scene_id] = "completed"
        for shot in shots:
            if shot["scene_id"] != scene_id:
                continue
            if not _should_process_shot(state, shot):
                continue
            shot["status"] = "completed"
            shot_status[shot["id"]] = "completed"

    updates: WorkflowState = {
        "scenes": scenes,  # type: ignore[typeddict-item]
        "shots": shots,  # type: ignore[typeddict-item]
        "scene_status": scene_status,
        "shot_status": shot_status,
        "retry_counts": retry_counts,
        "errors": errors,
        "current_step": step,  # type: ignore[typeddict-item]
    }

    if failed_item:
        if retry_counts.get(failed_item, 0) <= max_retries:
            updates["status"] = "paused"
            updates["pause_reason"] = f"Failed {failed_item}; awaiting resume/retry"
        else:
            updates["status"] = "failed"
            updates["pause_reason"] = f"Exceeded retries for {failed_item}"
    else:
        updates["status"] = "running"
        updates["pause_reason"] = ""
        # Clear regenerate targets after a successful pass.
        updates["regenerate_scene_ids"] = []
        updates["regenerate_shot_ids"] = []
        updates["video_prompt_overrides"] = {}
        updates["video_motion_mode"] = "auto"

    return updates


def director_planning(state: WorkflowState) -> WorkflowState:
    from app.orchestration.director_ai import (
        groq_enabled,
        load_project_story,
        plan_with_groq,
    )
    from app.workers.project_context import load_director_plan_for_production

    # Prefer the user-confirmed AI Director plan saved on the project.
    saved = load_director_plan_for_production(state["project_id"])
    if saved is not None:
        plan, scenes, shots = saved
        return {
            "director_plan": plan,
            "scenes": scenes,
            "shots": shots,
            "scene_status": _status_map(scenes),
            "shot_status": _status_map(shots),
            "current_step": "director_planning",
            "status": "running",
            "pause_reason": "",
        }

    # Keep regenerates on existing plan; fresh Groq only when starting (no scenes yet).
    if groq_enabled() and not (state.get("scenes") or []):
        meta = load_project_story(state["project_id"])
        try:
            plan, scenes, shots = plan_with_groq(
                project_id=state["project_id"],
                story=meta["story"],
                language=meta["language"],
                genre=meta["genre"],
                duration_seconds=meta["duration_seconds"],
            )
            return {
                "director_plan": plan,
                "scenes": scenes,
                "shots": shots,
                "scene_status": _status_map(scenes),
                "shot_status": _status_map(shots),
                "current_step": "director_planning",
                "status": "running",
                "pause_reason": "",
            }
        except Exception as exc:
            # Robust fallback — never block production on LLM flake.
            plan = mock_director_plan(state["project_id"])
            plan["error"] = str(exc)[:200]
            plan["provider"] = "mock_fallback"
            return {
                "director_plan": plan,
                "current_step": "director_planning",
                "status": "running",
                "pause_reason": "",
            }

    plan = mock_director_plan(state["project_id"])
    plan["provider"] = "mock"
    return {
        "director_plan": plan,
        "current_step": "director_planning",
        "status": "running",
        "pause_reason": "",
    }


def scene_planning(state: WorkflowState) -> WorkflowState:
    # Groq already filled scenes in director_planning — skip remock unless regenerating.
    existing = state.get("scenes") or []
    regen = state.get("regenerate_scene_ids") or []
    if existing and not regen:
        return {
            "scenes": existing,
            "scene_status": _status_map(existing),
            "current_step": "scene_planning",
            "status": "running",
        }
    scenes = mock_scenes(
        regenerate_scene_ids=state.get("regenerate_scene_ids"),
        existing=state.get("scenes"),
    )
    return {
        "scenes": scenes,
        "scene_status": _status_map(scenes),
        "current_step": "scene_planning",
        "status": "running",
    }


def shot_planning(state: WorkflowState) -> WorkflowState:
    existing = state.get("shots") or []
    regen_s = state.get("regenerate_scene_ids") or []
    regen_h = state.get("regenerate_shot_ids") or []
    if existing and not regen_s and not regen_h:
        return {
            "shots": existing,
            "shot_status": _status_map(existing),
            "current_step": "shot_planning",
            "status": "running",
        }
    shots = mock_shots(
        state.get("scenes") or [],
        regenerate_scene_ids=state.get("regenerate_scene_ids"),
        regenerate_shot_ids=state.get("regenerate_shot_ids"),
        existing=state.get("shots"),
    )
    return {
        "shots": shots,
        "shot_status": _status_map(shots),
        "current_step": "shot_planning",
        "status": "running",
    }


def asset_planning(state: WorkflowState) -> WorkflowState:
    """Flux stills for the active story scene only (one beat at a time)."""
    from app.orchestration.job_bridge import maybe_await_or_reconcile, reconcile_pending_jobs
    from app.orchestration.story_flow import active_scene, scene_needs_images, shots_for_scene
    from app.workers.dispatch import dispatch_image_jobs

    step = "asset_planning"
    working: WorkflowState = dict(state)  # type: ignore[assignment]
    if working.get("awaiting_jobs"):
        return reconcile_pending_jobs(working, step=step)

    scene = active_scene(working)
    if scene is None:
        return {
            **working,
            "awaiting_jobs": False,
            "pending_job_ids": [],
            "status": "running",
            "pause_reason": "",
            "current_step": step,
            "active_scene_id": "",
        }

    scene_id = str(scene["id"])
    working["active_scene_id"] = scene_id

    if not scene_needs_images(working, scene_id):
        # Stills already ready — advance straight to video for this beat.
        return {
            **working,
            "awaiting_jobs": False,
            "pending_job_ids": [],
            "status": "running",
            "pause_reason": "",
            "current_step": step,
            "active_scene_id": scene_id,
        }

    image_ready = set(working.get("image_ready_shot_ids") or [])
    regen_shots = set(working.get("regenerate_shot_ids") or [])
    regen_scenes = set(working.get("regenerate_scene_ids") or [])
    shots_for_images = []
    for shot in shots_for_scene(working, scene_id):
        sid = str(shot["id"])
        force = sid in regen_shots or scene_id in regen_scenes
        if force or sid not in image_ready:
            if force:
                image_ready.discard(sid)
            shots_for_images.append(shot)
    working["image_ready_shot_ids"] = sorted(image_ready)

    if not shots_for_images:
        return {
            **working,
            "awaiting_jobs": False,
            "pending_job_ids": [],
            "status": "running",
            "pause_reason": "",
            "current_step": step,
            "active_scene_id": scene_id,
        }

    job_ids = dispatch_image_jobs(
        working["project_id"],
        shots_for_images,
        regen_token=working.get("regen_token"),
    )
    result = maybe_await_or_reconcile(working, step=step, job_ids=job_ids)
    return {**result, "active_scene_id": scene_id}


def video_generation_planning(state: WorkflowState) -> WorkflowState:
    """Wan clips for the active story scene only; then hand off to next beat."""
    from app.orchestration.job_bridge import (
        jobs_terminal,
        maybe_await_or_reconcile,
        reconcile_pending_jobs,
    )
    from app.orchestration.story_flow import (
        active_scene,
        next_active_scene_id,
        scene_needs_video,
        shots_for_scene,
    )
    from app.workers.dispatch import dispatch_shot_video_jobs

    step = "video_generation_planning"
    working: WorkflowState = dict(state)  # type: ignore[assignment]

    if working.get("awaiting_jobs"):
        reconciled = reconcile_pending_jobs(working, step=step)
        if reconciled.get("awaiting_jobs"):
            return reconciled
        if reconciled.get("status") == "failed":
            return reconciled
        if reconciled.get("status") == "paused" and (
            reconciled.get("pause_reason") or ""
        ).startswith("Failed"):
            return reconciled
        working = {**working, **reconciled}
        # Point cursor at the next incomplete beat (or clear when story visuals done).
        working["active_scene_id"] = next_active_scene_id(working) or ""
        return {
            **working,
            "awaiting_jobs": False,
            "pending_job_ids": [],
            "status": working.get("status") or "running",
            "pause_reason": working.get("pause_reason") or "",
            "current_step": step,
        }

    scene = active_scene(working)
    if scene is None:
        return {
            **working,
            "awaiting_jobs": False,
            "pending_job_ids": [],
            "status": "running",
            "pause_reason": "",
            "current_step": step,
            "active_scene_id": "",
        }

    scene_id = str(scene["id"])
    working["active_scene_id"] = scene_id

    if not scene_needs_video(working, scene_id):
        return {
            **working,
            "active_scene_id": next_active_scene_id(working) or "",
            "awaiting_jobs": False,
            "pending_job_ids": [],
            "status": "running",
            "pause_reason": "",
            "current_step": step,
        }

    shot_status = working.get("shot_status") or {}
    shots = [
        shot
        for shot in shots_for_scene(working, scene_id)
        if shot_status.get(shot["id"]) != "completed"
    ]
    if not shots:
        return {
            **working,
            "active_scene_id": next_active_scene_id(working) or "",
            "awaiting_jobs": False,
            "pending_job_ids": [],
            "status": "running",
            "pause_reason": "",
            "current_step": step,
        }

    fail_shot_ids = set(working.get("fail_shot_ids") or [])
    retry_counts = dict(working.get("retry_counts") or {})
    fail_shots = set(fail_shot_ids)
    if scene_id in (working.get("fail_scene_ids") or []) and retry_counts.get(
        scene_id, 0
    ) < 1:
        for shot in shots:
            fail_shots.add(shot["id"])

    attempt = retry_counts.get(scene_id, 0)
    annotated = [{**shot, "_attempt": attempt} for shot in shots]
    overrides = dict(working.get("video_prompt_overrides") or {})
    motion_mode = str(working.get("video_motion_mode") or "auto")
    job_ids = dispatch_shot_video_jobs(
        working["project_id"],
        annotated,
        fail_shot_ids=fail_shots,
        regen_token=working.get("regen_token"),
        prompt_overrides=overrides,
        motion_mode=motion_mode,
    )

    if not jobs_terminal(job_ids):
        return {
            **maybe_await_or_reconcile(working, step=step, job_ids=job_ids),
            "active_scene_id": scene_id,
        }

    reconciled = maybe_await_or_reconcile(working, step=step, job_ids=job_ids)
    working = {**working, **reconciled}
    if working.get("status") in {"paused", "failed"}:
        return {**working, "active_scene_id": scene_id}

    working["active_scene_id"] = next_active_scene_id(working) or ""
    return {
        **working,
        "awaiting_jobs": False,
        "pending_job_ids": [],
        "status": "running",
        "pause_reason": "",
        "current_step": step,
    }


def audio_planning(state: WorkflowState) -> WorkflowState:
    from app.orchestration.job_bridge import maybe_await_or_reconcile, reconcile_pending_jobs
    from app.workers.dispatch import dispatch_audio_jobs

    step = "audio_planning"
    if state.get("awaiting_jobs"):
        return reconcile_pending_jobs(state, step=step)

    scenes = list(state.get("scenes") or [])
    regen_scenes = set(state.get("regenerate_scene_ids") or [])
    regen_shots = set(state.get("regenerate_shot_ids") or [])
    if regen_scenes or regen_shots:
        parent_from_shots = {
            shot.get("scene_id")
            for shot in state.get("shots") or []
            if shot.get("id") in regen_shots
        }
        target = regen_scenes | {s for s in parent_from_shots if s}
        scenes = [s for s in scenes if s.get("id") in target]

    language = None
    genre = None
    try:
        from app.workers.project_context import get_project_doc

        project = get_project_doc(state["project_id"]) or {}
        language = project.get("language")
        genre = project.get("genre")
    except Exception:
        pass
    job_ids = dispatch_audio_jobs(
        state["project_id"],
        scenes,
        language=language,
        genre=genre,
        regen_token=state.get("regen_token"),
    )
    return maybe_await_or_reconcile(state, step=step, job_ids=job_ids)


def assembly_planning(state: WorkflowState) -> WorkflowState:
    from app.orchestration.job_bridge import maybe_await_or_reconcile, reconcile_pending_jobs
    from app.workers.dispatch import dispatch_assembly_jobs

    step = "assembly_planning"
    if state.get("awaiting_jobs"):
        return reconcile_pending_jobs(state, step=step)

    scenes = list(state.get("scenes") or [])
    retry_counts = dict(state.get("retry_counts") or {})
    regen_scenes = list(state.get("regenerate_scene_ids") or [])
    regen_shots = set(state.get("regenerate_shot_ids") or [])
    if regen_shots:
        for shot in state.get("shots") or []:
            if shot.get("id") in regen_shots and shot.get("scene_id"):
                sid = str(shot["scene_id"])
                if sid not in regen_scenes:
                    regen_scenes.append(sid)

    job_ids = dispatch_assembly_jobs(
        state["project_id"],
        scenes=scenes,
        regenerate_scene_ids=regen_scenes or None,
        retry_counts=retry_counts,
        regen_token=state.get("regen_token"),
    )
    return maybe_await_or_reconcile(state, step=step, job_ids=job_ids)


def finalization(state: WorkflowState) -> WorkflowState:
    scenes = [dict(scene) for scene in state.get("scenes") or []]
    shots = [dict(shot) for shot in state.get("shots") or []]
    for scene in scenes:
        if scene["status"] != "failed":
            scene["status"] = "completed"
    for shot in shots:
        if shot["status"] != "failed":
            shot["status"] = "completed"
    return {
        "scenes": scenes,  # type: ignore[typeddict-item]
        "shots": shots,  # type: ignore[typeddict-item]
        "scene_status": _status_map(scenes),
        "shot_status": _status_map(shots),
        "current_step": "finalization",
        "status": "completed",
        "pause_reason": "",
        "awaiting_jobs": False,
        "pending_job_ids": [],
        "regenerate_scene_ids": [],
        "regenerate_shot_ids": [],
        "video_prompt_overrides": {},
        "video_motion_mode": "auto",
        "active_scene_id": "",
        "regen_token": "",
    }


def pause_if_needed(state: WorkflowState) -> WorkflowState:
    """Interrupt the graph when a recoverable failure paused the workflow."""
    from langgraph.types import interrupt

    if state.get("status") == "paused":
        interrupt(
            {
                "project_id": state.get("project_id"),
                "reason": state.get("pause_reason") or "paused",
                "current_step": state.get("current_step"),
            }
        )
        # On resume, clear pause so the pipeline can continue / retry.
        return {
            "status": "running",
            "pause_reason": "",
        }
    return {}
