"""Story-order scene cursor: capture one scene (stills→clips) before the next."""

from __future__ import annotations

from typing import Any

from app.orchestration.state import WorkflowState


def recommended_scene_count(duration_seconds: int) -> int:
    """Scale scene count with runtime (~25s/scene). Cap for one LLM plan call."""
    duration = max(10, int(duration_seconds))
    # Short clips: 2–4 scenes. Longer: up to 24 narrative beats.
    raw = max(2, (duration + 24) // 25)
    if duration <= 45:
        return max(2, min(3, raw))
    if duration <= 120:
        return max(3, min(6, raw))
    if duration <= 600:
        return max(6, min(16, raw))
    return max(12, min(24, raw))


def sorted_scenes(state: WorkflowState) -> list[dict[str, Any]]:
    return sorted(list(state.get("scenes") or []), key=lambda s: s.get("order", 0))


def scene_ids_in_order(state: WorkflowState) -> list[str]:
    return [str(s["id"]) for s in sorted_scenes(state)]


def _regen_scene_ids(state: WorkflowState) -> set[str]:
    regen_scenes = set(state.get("regenerate_scene_ids") or [])
    regen_shots = set(state.get("regenerate_shot_ids") or [])
    if not regen_scenes and not regen_shots:
        return set()
    out = set(regen_scenes)
    for shot in state.get("shots") or []:
        if shot.get("id") in regen_shots and shot.get("scene_id"):
            out.add(str(shot["scene_id"]))
    return out


def scenes_to_produce(state: WorkflowState) -> list[dict[str, Any]]:
    """Scenes still needing visual production, in story order."""
    regen = _regen_scene_ids(state)
    scenes = sorted_scenes(state)
    shot_status = state.get("shot_status") or {}
    out: list[dict[str, Any]] = []
    for scene in scenes:
        sid = str(scene["id"])
        if regen and sid not in regen:
            continue
        related = [
            sh for sh in (state.get("shots") or []) if str(sh.get("scene_id")) == sid
        ]
        # Visual beat done once every shot has a completed clip — even if still
        # listed in regenerate_* (keeps story loop from spinning forever).
        if related and all(shot_status.get(sh["id"]) == "completed" for sh in related):
            continue
        if not related and (
            (state.get("scene_status") or {}).get(sid) == "completed"
            or scene.get("status") == "completed"
        ):
            continue
        out.append(scene)
    return out


def active_scene(state: WorkflowState) -> dict[str, Any] | None:
    """Current story beat to capture (stills then clips)."""
    cursor = str(state.get("active_scene_id") or "").strip()
    todo = scenes_to_produce(state)
    if not todo:
        return None
    if cursor:
        for scene in todo:
            if str(scene["id"]) == cursor:
                return scene
    return todo[0]


def shots_for_scene(state: WorkflowState, scene_id: str) -> list[dict[str, Any]]:
    shots = [
        sh
        for sh in (state.get("shots") or [])
        if str(sh.get("scene_id")) == str(scene_id)
    ]
    return sorted(shots, key=lambda s: s.get("order", 0))


def scene_needs_images(state: WorkflowState, scene_id: str) -> bool:
    """True if any shot in the scene is not yet video-complete (needs still pass)."""
    shot_status = state.get("shot_status") or {}
    related = shots_for_scene(state, scene_id)
    if not related:
        return False
    # Regen targets always re-image.
    regen_shots = set(state.get("regenerate_shot_ids") or [])
    regen_scenes = set(state.get("regenerate_scene_ids") or [])
    if str(scene_id) in regen_scenes:
        return True
    for sh in related:
        if sh["id"] in regen_shots:
            return True
        if shot_status.get(sh["id"]) != "completed":
            # Image jobs don't mark shot completed; use image_ready set when present.
            ready = set(state.get("image_ready_shot_ids") or [])
            if sh["id"] not in ready:
                return True
    return False


def scene_needs_video(state: WorkflowState, scene_id: str) -> bool:
    shot_status = state.get("shot_status") or {}
    related = shots_for_scene(state, scene_id)
    if not related:
        return False
    regen_shots = set(state.get("regenerate_shot_ids") or [])
    regen_scenes = set(state.get("regenerate_scene_ids") or [])
    for sh in related:
        if sh["id"] in regen_shots or str(scene_id) in regen_scenes:
            if shot_status.get(sh["id"]) != "completed":
                return True
            # Force regen: treat as needing video even if previously completed
            # (caller resets status to pending before invoke).
        if shot_status.get(sh["id"]) != "completed":
            return True
    return False


def story_beats_remaining(state: WorkflowState) -> bool:
    """True when at least one scene still needs stills and/or clips."""
    return bool(scenes_to_produce(state))


def next_active_scene_id(state: WorkflowState) -> str | None:
    """Next story beat id (first incomplete scene in order)."""
    todo = scenes_to_produce(state)
    if not todo:
        return None
    return str(todo[0]["id"])
