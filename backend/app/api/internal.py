"""Internal worker → API callbacks (shared-secret auth)."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status
from langgraph.types import Command

from app.core.config import get_settings
from app.orchestration import runner
from app.orchestration.checkpointing import thread_config
from app.orchestration.graph import compile_graph
from app.workers import job_store

router = APIRouter(prefix="/api/internal", tags=["internal"])


def _verify_internal_token(token: str | None) -> None:
    settings = get_settings()
    expected = settings.internal_api_token.encode("utf-8")
    provided = (token or "").encode("utf-8")
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token",
        )


def _db():
    settings = get_settings()
    return job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)


async def maybe_resume_project_from_jobs(project_id: str) -> bool:
    """If graph is interrupted awaiting jobs and all are terminal, resume."""
    graph = compile_graph()
    config = thread_config(project_id)
    snapshot = graph.get_state(config)
    if not snapshot.values:
        return False
    values = snapshot.values
    if not values.get("awaiting_jobs"):
        return False
    pending = list(values.get("pending_job_ids") or [])
    if not pending or not job_store.jobs_all_terminal(_db(), pending):
        return False
    if not snapshot.interrupts:
        return False
    try:
        runner._schedule(project_id, Command(resume=True))
    except HTTPException:
        return False
    return True


@router.post("/jobs/{job_id}/complete")
async def job_complete(
    job_id: str,
    payload: dict[str, Any] | None = None,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> dict[str, Any]:
    _verify_internal_token(x_internal_token)
    job = job_store.get_job(_db(), job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    project_id = job["project_id"]
    resumed = await maybe_resume_project_from_jobs(project_id)
    return {
        "ok": True,
        "jobId": job_id,
        "projectId": project_id,
        "resumed": resumed,
        "source": (payload or {}).get("source"),
    }
