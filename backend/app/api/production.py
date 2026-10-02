from typing import Any

from fastapi import APIRouter, Depends
from pymongo.asynchronous.database import AsyncDatabase

from app.db.mongodb import get_db
from app.orchestration import runner
from app.schemas.auth import UserResponse
from app.schemas.production import (
    ProductionAcceptedResponse,
    ProductionRegenerateRequest,
    ProductionStartRequest,
)
from app.services.auth_service import get_current_user
from app.services import project_service

router = APIRouter(prefix="/api/projects", tags=["production"])


@router.post(
    "/{project_id}/production/start",
    response_model=ProductionAcceptedResponse,
    status_code=202,
)
async def start_production(
    project_id: str,
    payload: ProductionStartRequest | None = None,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    body = payload or ProductionStartRequest()
    # Ownership check via project fetch inside runner.
    return await runner.start_production(
        db,
        current_user,
        project_id,
        fail_scene_ids=body.fail_scene_ids,
        max_retries=body.max_retries,
    )


@router.get("/{project_id}/jobs")
async def list_project_jobs(
    project_id: str,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    await project_service.get_project(db, current_user, project_id)
    from app.core.config import get_settings
    from app.workers import job_store

    settings = get_settings()
    sync_db = job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)
    jobs = job_store.list_jobs_for_project(sync_db, project_id)
    return {"projectId": project_id, "jobs": jobs}


@router.get("/{project_id}/production/status")
async def production_status(
    project_id: str,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    await project_service.get_project(db, current_user, project_id)
    return await runner.get_production_status(project_id)


@router.post(
    "/{project_id}/production/resume",
    response_model=ProductionAcceptedResponse,
    status_code=202,
)
async def resume_production(
    project_id: str,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    await project_service.get_project(db, current_user, project_id)
    return await runner.resume_production(project_id)


@router.post(
    "/{project_id}/production/regenerate",
    status_code=202,
)
async def regenerate_production(
    project_id: str,
    payload: ProductionRegenerateRequest,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    await project_service.get_project(db, current_user, project_id)
    result = await runner.regenerate_production(
        project_id,
        scene_ids=payload.scene_ids,
        shot_ids=payload.shot_ids,
    )
    return {
        "projectId": result["project_id"],
        "status": result["status"],
        "message": result["message"],
        "sceneIds": result.get("scene_ids", []),
        "shotIds": result.get("shot_ids", []),
    }
