from __future__ import annotations

from typing import Any

from app.orchestration.state import PIPELINE_STEPS, WorkflowState


def production_status_payload(state: WorkflowState | dict[str, Any]) -> dict[str, Any]:
    current = state.get("current_step") or ""
    status = state.get("status") or "pending"
    steps: list[dict[str, str]] = []
    seen_current = False

    for step in PIPELINE_STEPS:
        if status == "completed":
            step_status = "completed"
        elif not current:
            step_status = "pending"
        elif step == current:
            step_status = "running" if status == "running" else str(status)
            seen_current = True
        elif not seen_current:
            step_status = "completed"
        else:
            step_status = "pending"
        steps.append({"id": step, "status": step_status})

    scenes = state.get("scenes") or []
    shots = state.get("shots") or []
    return {
        "projectId": state.get("project_id"),
        "status": status,
        "currentStep": current or None,
        "pauseReason": state.get("pause_reason") or None,
        "steps": steps,
        "scenes": [
            {
                "id": scene["id"],
                "order": scene["order"],
                "title": scene["title"],
                "status": scene["status"],
                "durationSeconds": scene["duration_seconds"],
            }
            for scene in scenes
        ],
        "shots": [
            {
                "id": shot["id"],
                "sceneId": shot["scene_id"],
                "order": shot["order"],
                "title": shot["title"],
                "status": shot["status"],
            }
            for shot in shots
        ],
        "sceneStatus": state.get("scene_status") or {},
        "shotStatus": state.get("shot_status") or {},
        "retryCounts": state.get("retry_counts") or {},
        "errors": state.get("errors") or [],
        "maxRetries": state.get("max_retries", 2),
    }
