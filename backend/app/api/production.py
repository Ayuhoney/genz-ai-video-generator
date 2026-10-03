from typing import Any

from fastapi import APIRouter, Depends
from pymongo.asynchronous.database import AsyncDatabase

from app.db.mongodb import get_db
from app.orchestration import runner
from app.schemas.auth import UserResponse
from app.schemas.production import (
    ProductionAcceptedResponse,
    ProductionFixShotRequest,
    ProductionRegenerateRequest,
    ProductionResumeMissingRequest,
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
        include_stills=bool(payload.include_stills or payload.scene_ids),
        motion_mode="auto",
    )
    return {
        "projectId": result["project_id"],
        "status": result["status"],
        "message": result["message"],
        "sceneIds": result.get("scene_ids", []),
        "shotIds": result.get("shot_ids", []),
    }


@router.post(
    "/{project_id}/production/fix-shot",
    status_code=202,
)
async def fix_shot_production(
    project_id: str,
    payload: ProductionFixShotRequest,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    """Retry a failed shot with safe motion, user notes, or a new still."""
    await project_service.get_project(db, current_user, project_id)
    result = await runner.fix_shot_production(
        project_id,
        shot_id=payload.shot_id,
        mode=payload.mode,
        guidance=payload.guidance,
    )
    return {
        "projectId": result["project_id"],
        "status": result["status"],
        "message": result["message"],
        "mode": result.get("mode"),
        "shotIds": result.get("shot_ids", []),
        "motionPrompt": result.get("motionPrompt"),
    }


@router.get("/{project_id}/production/resume-missing")
async def inventory_resume_missing(
    project_id: str,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    """Report planned shots vs existing assets + estimated cost of missing work."""
    await project_service.get_project(db, current_user, project_id)
    from app.workers.resume_missing import build_inventory, format_inventory_table

    inv = build_inventory(project_id)
    return {
        "projectId": project_id,
        "plannedShotCount": inv.planned_shot_count,
        "estimatedCostUsd": inv.estimated_cost_usd,
        "report": format_inventory_table(inv),
        "rows": [
            {
                "shotId": r.shot_id,
                "sceneId": r.scene_id,
                "plannedText": r.planned_text,
                "imageExists": r.image_exists,
                "clipExists": r.clip_exists,
                "ttsExists": r.tts_exists,
                "sfxExists": r.sfx_exists,
                "musicExists": r.music_exists,
            }
            for r in inv.rows
        ],
        "missingImageShotIds": inv.missing_image_shot_ids,
        "missingVideoShotIds": inv.missing_video_shot_ids,
        "missingTtsSceneIds": inv.missing_tts_scene_ids,
        "missingSfxSceneIds": inv.missing_sfx_scene_ids,
        "missingMusicSceneIds": inv.missing_music_scene_ids,
        "note": inv.note,
    }


@router.post("/{project_id}/production/resume-missing", status_code=202)
async def resume_missing_production(
    project_id: str,
    payload: ProductionResumeMissingRequest | None = None,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> dict[str, Any]:
    """Create jobs only for missing assets; reuse completed unless force=[...]."""
    await project_service.get_project(db, current_user, project_id)
    body = payload or ProductionResumeMissingRequest()
    from app.workers.resume_missing import run_resume_missing

    result = run_resume_missing(
        project_id,
        force_shot_ids=body.force,
        confirm=body.confirm,
        assemble=body.assemble,
    )
    status_code_hint = "accepted" if result.get("started") else "estimate"
    return {
        "projectId": project_id,
        "status": status_code_hint,
        "message": result.get("message"),
        "estimatedCostUsd": result.get("estimated_cost_usd"),
        "report": result.get("report"),
        "jobIds": result.get("job_ids") or [],
        "assembled": result.get("assembled"),
        "force": result.get("force_shot_ids") or [],
        "missingImageShotIds": result.get("missing_image_shot_ids") or [],
        "missingVideoShotIds": result.get("missing_video_shot_ids") or [],
        "reclaimedJobIds": result.get("reclaimedJobIds") or result.get("reclaimed_job_ids") or [],
    }
