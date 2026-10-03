from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.orchestration.state import PIPELINE_STEPS, WorkflowState

_POLICY_MARKERS = (
    "content_policy",
    "content checker",
    "content_rejected",
    "couldn't be processed as-is",
    "adjust the prompt",
    "needs_new_prompt",
    "needs_new_image",
)

# In-progress jobs that stop updating are treated as stuck (worker restart, lost Celery task).
_IN_PROGRESS_STATUSES = frozenset({"generating", "running", "uploading"})
_STALE_SECONDS_BY_TYPE = {
    "video": 12 * 60,
    "image": 8 * 60,
    "tts": 8 * 60,
    "sfx": 8 * 60,
    "music": 10 * 60,
    "scene_render": 10 * 60,
    "final_assembly": 15 * 60,
}
_DEFAULT_STALE_SECONDS = 12 * 60


def _parse_job_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def stale_seconds_for_job(job: dict[str, Any]) -> int:
    job_type = str(job.get("type") or "")
    return int(_STALE_SECONDS_BY_TYPE.get(job_type, _DEFAULT_STALE_SECONDS))


def is_stale_in_progress_job(
    job: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """True when a generating/running job has not updated within its stale window."""
    status = str(job.get("status") or "")
    if status not in _IN_PROGRESS_STATUSES:
        return False
    stamp = _parse_job_time(job.get("updated_at")) or _parse_job_time(
        job.get("started_at")
    )
    if stamp is None:
        return False
    current = now or datetime.now(UTC)
    age = (current - stamp).total_seconds()
    return age >= stale_seconds_for_job(job)


def classify_job_issue(job: dict[str, Any]) -> dict[str, Any] | None:
    """Map a failed job into a user-facing issue for the project UI."""
    status = str(job.get("status") or "")
    if status in {"completed", "succeeded", "pending", "running", "queued", "generating", "uploading"}:
        return None
    if status not in {
        "failed",
        "timed_out",
        "needs_new_image",
        "needs_new_prompt",
        "failed_retryable",
    }:
        return None

    error = str(job.get("error") or "")
    error_class = str(job.get("error_class") or "")
    blob = f"{status} {error_class} {error}".lower()
    # Internal reclaim / intentional-skip markers — not user-facing failures.
    if any(
        m in blob
        for m in (
            "superseded_after_worker_restart",
            "reclaimed_for_resume",
            "reclaimed_stale_on_worker_boot",
            "worker_restart_orphaned_reset",
            "sfx_fal_hang_skipped",
            "sfx_skipped_for_assembly",
            "sfx_skipped",
        )
    ):
        return None
    shot_id = job.get("shot_id")
    scene_id = job.get("scene_id")
    job_type = str(job.get("type") or "unknown")

    if status == "needs_new_image" or (
        "image_url" in blob and any(m in blob for m in _POLICY_MARKERS)
    ):
        code = "needs_new_image"
        action = "regenerate_still"
        message = (
            "Safety filter blocked this still. "
            "Use “New still + clip”, or “AI auto-fix” for a safe motion retry."
        )
    elif (
        status == "needs_new_prompt"
        or error_class.upper() == "CONTENT_REJECTED"
        or any(m in blob for m in _POLICY_MARKERS)
    ):
        code = "content_policy"
        action = "auto_fix"
        message = (
            "Safety filter blocked this clip. "
            "Try “AI auto-fix” (safe camera-only prompt), add your own camera/mood notes, "
            "or make a new still — don’t keep retrying the same story prompt."
        )
    elif status in {"failed_retryable", "timed_out"} or "retry" in blob:
        code = "retryable"
        action = "retry"
        message = (
            "Temporary generation issue. You can Retry — this is usually network/timeout, not safety."
        )
    else:
        code = "failed"
        action = "retry"
        message = error or (
            "This step failed. Retry, use AI auto-fix, or regenerate the still."
        )

    return {
        "shotId": shot_id,
        "sceneId": scene_id,
        "jobType": job_type,
        "jobStatus": status,
        "code": code,
        "action": action,
        "message": message,
        "error": error[:280] if error else None,
    }


def build_stuck_production_issue(
    stale_jobs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """One project-level issue so the UI can offer Resume from here."""
    if not stale_jobs:
        return None
    types = sorted({str(j.get("type") or "job") for j in stale_jobs})
    shot_ids = sorted(
        {str(j["shot_id"]) for j in stale_jobs if j.get("shot_id")}
    )
    type_label = ", ".join(types[:3]) + ("…" if len(types) > 3 else "")
    n = len(stale_jobs)
    return {
        "shotId": None,
        "sceneId": None,
        "jobType": "production",
        "jobStatus": "stuck",
        "code": "stuck",
        "action": "resume_missing",
        "message": (
            f"Production looks stuck — {n} {type_label} job"
            f"{'' if n == 1 else 's'} stopped updating "
            "(often after a worker restart). "
            "Use “Resume from here” to continue; finished images/clips are kept."
        ),
        "error": None,
        "stuckJobCount": n,
        "stuckShotIds": shot_ids,
    }


def _job_succeeded(job: dict[str, Any]) -> bool:
    return str(job.get("status") or "") in {"completed", "succeeded"}


def collect_project_issues(project_id: str) -> list[dict[str, Any]]:
    """Latest failure issue per shot/scene from Mongo jobs (for UI guidance)."""
    try:
        from app.core.config import get_settings
        from app.workers import job_store
    except Exception:
        return []

    settings = get_settings()
    db = job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)
    jobs = job_store.list_jobs_for_project(db, project_id)

    # Final video done → don't nag about superseded/skipped retries.
    try:
        from app.workers.project_context import get_project_doc

        project = get_project_doc(project_id) or {}
        if str(project.get("status") or "") == "completed":
            return []
    except Exception:
        pass

    # Later successful jobs clear earlier failures for the same shot/scene/type.
    ok_shot_types: set[tuple[str, str]] = set()
    ok_scene_types: set[tuple[str, str]] = set()
    for job in jobs:
        if not _job_succeeded(job):
            continue
        job_type = str(job.get("type") or "")
        if job.get("shot_id"):
            ok_shot_types.add((job_type, str(job["shot_id"])))
        elif job.get("scene_id"):
            ok_scene_types.add((job_type, str(job["scene_id"])))

    now = datetime.now(UTC)
    stale = [j for j in jobs if is_stale_in_progress_job(j, now=now)]
    stuck_issue = build_stuck_production_issue(stale)

    # Prefer newest failure per (type, shot/scene).
    ranked: dict[str, dict[str, Any]] = {}
    for job in jobs:
        issue = classify_job_issue(job)
        if not issue:
            continue
        job_type = str(issue.get("jobType") or "")
        shot_id = issue.get("shotId")
        scene_id = issue.get("sceneId")
        if shot_id and (job_type, str(shot_id)) in ok_shot_types:
            continue
        if (not shot_id) and scene_id and (job_type, str(scene_id)) in ok_scene_types:
            continue
        key = f"{job_type}:{shot_id or scene_id or job.get('id')}"
        ranked[key] = issue
    # Deduplicate to one issue per shot (prefer video over image).
    by_shot: dict[str, dict[str, Any]] = {}
    orphans: list[dict[str, Any]] = []
    for issue in ranked.values():
        sid = issue.get("shotId")
        if not sid:
            orphans.append(issue)
            continue
        prev = by_shot.get(str(sid))
        if prev is None or (
            prev.get("jobType") != "video" and issue.get("jobType") == "video"
        ):
            by_shot[str(sid)] = issue
    issues = list(by_shot.values()) + orphans
    if stuck_issue:
        issues.insert(0, stuck_issue)
    return issues


def production_status_payload(state: WorkflowState | dict[str, Any]) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {
            "projectId": None,
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
            "issues": [],
            "maxRetries": 2,
            "activeSceneId": None,
        }
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
        "activeSceneId": state.get("active_scene_id") or None,
        "issues": collect_project_issues(str(state.get("project_id") or "")),
    }
