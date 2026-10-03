"""Resume / reuse: inventory existing assets and dispatch only missing work."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.storage import get_storage
from app.storage.asset_store import get_sync_db_from_settings, list_assets_for_project
from app.workers import job_store
from app.workers.project_context import load_director_plan_for_production


@dataclass
class ShotInventoryRow:
    scene_id: str
    shot_id: str
    planned_text: str
    image_exists: bool
    clip_exists: bool
    tts_exists: bool
    sfx_exists: bool = False
    music_exists: bool = False


@dataclass
class ResumeInventory:
    project_id: str
    planned_shot_count: int
    rows: list[ShotInventoryRow] = field(default_factory=list)
    missing_image_shot_ids: list[str] = field(default_factory=list)
    missing_video_shot_ids: list[str] = field(default_factory=list)
    missing_tts_scene_ids: list[str] = field(default_factory=list)
    missing_sfx_scene_ids: list[str] = field(default_factory=list)
    missing_music_scene_ids: list[str] = field(default_factory=list)
    scenes: list[dict[str, Any]] = field(default_factory=list)
    shots: list[dict[str, Any]] = field(default_factory=list)
    estimated_cost_usd: float = 0.0
    note: str = ""


def _asset_on_disk(asset: dict[str, Any] | None) -> bool:
    if not asset:
        return False
    key = str(asset.get("r2_key") or "")
    if not key:
        return False
    try:
        return bool(get_storage().exists(key))
    except Exception:
        return False


def _ok_job_ids(db: Any, project_id: str, task_type: str) -> set[str]:
    return {
        str(j["id"])
        for j in job_store.list_jobs_for_project(db, project_id)
        if j.get("type") == task_type and j.get("status") in job_store.SUCCESS_STATUSES
    }


def latest_successful_asset(
    assets: list[dict[str, Any]],
    *,
    asset_type: str,
    ok_job_ids: set[str],
    scene_id: str | None = None,
    shot_id: str | None = None,
    allow_scene_image_fallback: bool = False,
) -> dict[str, Any] | None:
    """Latest asset on disk from a successful job (or no job_id), scoped to scene/shot."""
    matches: list[dict[str, Any]] = []
    for a in assets:
        if a.get("type") != asset_type:
            continue
        if scene_id is not None and a.get("scene_id") != scene_id:
            continue
        a_shot = a.get("shot_id")
        if shot_id:
            if a_shot == shot_id:
                matches.append(a)
            elif (
                allow_scene_image_fallback
                and asset_type == "image"
                and not a_shot
            ):
                matches.append(a)
        elif shot_id is None:
            matches.append(a)
    if not matches:
        return None
    # Prefer exact shot match over scene-level fallback.
    if shot_id:
        exact = [a for a in matches if a.get("shot_id") == shot_id]
        if exact:
            matches = exact
    good = [
        a
        for a in matches
        if not a.get("job_id") or str(a.get("job_id")) in ok_job_ids
    ]
    pool = good or matches
    for asset in reversed(pool):
        if _asset_on_disk(asset):
            return asset
    return None


def estimate_missing_cost_usd(
    *,
    missing_images: int,
    missing_videos: int,
    missing_tts: int,
    missing_sfx: int,
    missing_music: int,
) -> float:
    settings = get_settings()
    total = 0.0
    total += missing_images * float(getattr(settings, "fal_image_cost_usd", 0) or 0)
    total += missing_videos * float(getattr(settings, "fal_video_cost_usd", 0) or 0)
    total += missing_tts * float(getattr(settings, "sarvam_tts_cost_usd", 0) or 0)
    total += missing_sfx * float(getattr(settings, "fal_sfx_cost_usd", 0) or 0)
    total += missing_music * float(getattr(settings, "fal_music_cost_usd", 0) or 0)
    return round(total, 6)


def build_inventory(project_id: str) -> ResumeInventory:
    loaded = load_director_plan_for_production(project_id)
    if not loaded:
        raise ValueError(f"No confirmed director plan for project {project_id}")
    _plan, scenes, shots = loaded
    db = get_sync_db_from_settings()
    assets = list_assets_for_project(db, project_id)
    ok_image = _ok_job_ids(db, project_id, "image")
    ok_video = _ok_job_ids(db, project_id, "video")
    ok_tts = _ok_job_ids(db, project_id, "tts")
    ok_sfx = _ok_job_ids(db, project_id, "sfx")
    ok_music = _ok_job_ids(db, project_id, "music")

    from app.media.audio_timeline import dialogue_lines_from_scene

    # ONE project-level theme track (no scene_id).
    project_music_ok = any(
        a.get("type") == "music"
        and not a.get("scene_id")
        and (not a.get("job_id") or str(a.get("job_id")) in ok_music)
        and _asset_on_disk(a)
        for a in assets
    )
    if not project_music_ok:
        # Legacy per-scene music still counts so old projects resume.
        project_music_ok = any(
            a.get("type") == "music"
            and (not a.get("job_id") or str(a.get("job_id")) in ok_music)
            and _asset_on_disk(a)
            for a in assets
        )

    scene_tts: dict[str, bool] = {}
    scene_sfx: dict[str, bool] = {}
    scene_music: dict[str, bool] = {}
    for scene in scenes:
        sid = str(scene["id"])
        lines = dialogue_lines_from_scene(scene)
        if lines:
            have_lines = {
                str(
                    (a.get("metadata") or {}).get("line_id")
                    or a.get("shot_id")
                    or ""
                )
                for a in assets
                if a.get("type") == "tts"
                and a.get("scene_id") == sid
                and (not a.get("job_id") or str(a.get("job_id")) in ok_tts)
                and _asset_on_disk(a)
            }
            scene_tts[sid] = all(str(line["id"]) in have_lines for line in lines)
        else:
            scene_tts[sid] = (
                latest_successful_asset(
                    assets, asset_type="tts", ok_job_ids=ok_tts, scene_id=sid
                )
                is not None
            )
        scene_sfx[sid] = (
            latest_successful_asset(
                assets, asset_type="sfx", ok_job_ids=ok_sfx, scene_id=sid
            )
            is not None
        )
        scene_music[sid] = project_music_ok

    rows: list[ShotInventoryRow] = []
    missing_image: list[str] = []
    missing_video: list[str] = []
    for shot in shots:
        shot_id = str(shot["id"])
        scene_id = str(shot["scene_id"])
        text = str(shot.get("description") or shot.get("title") or shot_id)
        img = latest_successful_asset(
            assets,
            asset_type="image",
            ok_job_ids=ok_image,
            scene_id=scene_id,
            shot_id=shot_id,
            allow_scene_image_fallback=True,
        )
        clip = latest_successful_asset(
            assets,
            asset_type="video",
            ok_job_ids=ok_video,
            scene_id=scene_id,
            shot_id=shot_id,
        )
        image_ok = img is not None
        clip_ok = clip is not None
        if not image_ok:
            missing_image.append(shot_id)
        if not clip_ok:
            missing_video.append(shot_id)
        rows.append(
            ShotInventoryRow(
                scene_id=scene_id,
                shot_id=shot_id,
                planned_text=text,
                image_exists=image_ok,
                clip_exists=clip_ok,
                tts_exists=bool(scene_tts.get(scene_id)),
                sfx_exists=bool(scene_sfx.get(scene_id)),
                music_exists=bool(scene_music.get(scene_id)),
            )
        )

    missing_tts = [sid for sid, ok in scene_tts.items() if not ok]
    missing_sfx = [sid for sid, ok in scene_sfx.items() if not ok]
    missing_music = [sid for sid, ok in scene_music.items() if not ok]
    cost = estimate_missing_cost_usd(
        missing_images=len(missing_image),
        missing_videos=len(missing_video),
        missing_tts=len(missing_tts),
        missing_sfx=len(missing_sfx),
        missing_music=len(missing_music),
    )
    note = (
        f"Confirmed plan has {len(shots)} shots "
        f"({len(scenes)} scenes). Prior production stored scene-level stills "
        f"+ {sum(1 for r in rows if r.clip_exists)} video clips — not 12 planned shots."
    )
    return ResumeInventory(
        project_id=project_id,
        planned_shot_count=len(shots),
        rows=rows,
        missing_image_shot_ids=missing_image,
        missing_video_shot_ids=missing_video,
        missing_tts_scene_ids=missing_tts,
        missing_sfx_scene_ids=missing_sfx,
        missing_music_scene_ids=missing_music,
        scenes=scenes,
        shots=shots,
        estimated_cost_usd=cost,
        note=note,
    )


def format_inventory_table(inv: ResumeInventory) -> str:
    lines = [
        f"project={inv.project_id} planned_shots={inv.planned_shot_count} "
        f"est_missing_usd={inv.estimated_cost_usd:.4f}",
        f"{'shot_id':<10} | {'planned text':<48} | img | clip | tts",
        "-" * 90,
    ]
    for r in inv.rows:
        text = (r.planned_text[:45] + "…") if len(r.planned_text) > 46 else r.planned_text
        lines.append(
            f"{r.shot_id:<10} | {text:<48} | "
            f"{'Y' if r.image_exists else 'N':^3} | "
            f"{'Y' if r.clip_exists else 'N':^4} | "
            f"{'Y' if r.tts_exists else 'N':^3}"
        )
    lines.append(inv.note)
    lines.append(
        "missing: "
        f"images={inv.missing_image_shot_ids} "
        f"clips={inv.missing_video_shot_ids} "
        f"tts={inv.missing_tts_scene_ids} "
        f"sfx={inv.missing_sfx_scene_ids} "
        f"music={inv.missing_music_scene_ids}"
    )
    return "\n".join(lines)


def confirmation_granted(*, confirm_flag: bool = False) -> bool:
    if confirm_flag:
        return True
    env = (os.environ.get("CONFIRM_RESUME") or "").strip().lower()
    return env in {"1", "true", "yes", "y"}


def reclaim_in_progress_jobs(
    project_id: str,
    *,
    only_stale: bool = False,
) -> list[str]:
    """Reset generating/running jobs so Celery can pick them up again after a stall.

    Does not delete assets. Marks in-progress rows pending and clears celery ids.
    When only_stale=True, only jobs past their type-specific stale window are reset
    (safe for worker boot reclaim while healthy jobs keep running).
    """
    from datetime import UTC, datetime

    from app.orchestration.status import is_stale_in_progress_job

    db = get_sync_db_from_settings()
    now = datetime.now(UTC)
    reclaimed: list[str] = []
    for job in job_store.list_jobs_for_project(db, project_id):
        status = str(job.get("status") or "")
        if status not in {"generating", "running", "uploading"}:
            continue
        if only_stale and not is_stale_in_progress_job(job, now=now):
            continue
        job_id = str(job.get("id") or "")
        if not job_id:
            continue
        job_store.update_job(
            db,
            job_id,
            status="pending",
            clip_state="pending",
            progress=0,
            error="reclaimed_for_resume",
            celery_task_id=None,
            started_at=None,
            finished_at=None,
            updated_at=now,
        )
        reclaimed.append(job_id)
    return reclaimed


def reclaim_stale_jobs_all_projects() -> list[str]:
    """Worker boot helper: reclaim orphaned in-progress jobs across projects."""
    from datetime import UTC, datetime

    from app.orchestration.status import is_stale_in_progress_job

    db = get_sync_db_from_settings()
    now = datetime.now(UTC)
    reclaimed: list[str] = []
    cursor = db["jobs"].find(
        {"status": {"$in": ["generating", "running", "uploading"]}}
    )
    for doc in cursor:
        job = dict(doc)
        job["id"] = str(job.pop("_id"))
        if not is_stale_in_progress_job(job, now=now):
            continue
        job_store.update_job(
            db,
            job["id"],
            status="pending",
            clip_state="pending",
            progress=0,
            error="reclaimed_stale_on_worker_boot",
            celery_task_id=None,
            started_at=None,
            finished_at=None,
            updated_at=now,
        )
        reclaimed.append(job["id"])
    if reclaimed:
        from app.workers.dispatch import dispatch_jobs

        docs = [
            j
            for jid in reclaimed
            if (j := job_store.get_job(db, jid)) is not None
        ]
        if docs:
            dispatch_jobs(docs)
    return reclaimed


def run_resume_missing(
    project_id: str,
    *,
    force_shot_ids: list[str] | None = None,
    confirm: bool = False,
    assemble: bool = True,
) -> dict[str, Any]:
    """Create jobs only for missing assets (or explicit force shots); then ambient assembly.

    Never deletes assets. Completed assets are skipped unless listed in force_shot_ids.
    Requires confirm=True or CONFIRM_RESUME=true to enqueue work.
    """
    from app.workers.dispatch import (
        dispatch_assembly_jobs,
        dispatch_image_jobs,
        dispatch_jobs,
        dispatch_shot_video_jobs,
    )

    inv = build_inventory(project_id)
    force = {str(s) for s in (force_shot_ids or []) if s}
    report = format_inventory_table(inv)

    # Force list marks those shots as missing for image+video (explicit regen).
    image_targets = list(dict.fromkeys([*inv.missing_image_shot_ids, *force]))
    video_targets = list(dict.fromkeys([*inv.missing_video_shot_ids, *force]))

    # Recompute cost including forced regenerations.
    force_extra_images = len([s for s in force if s not in inv.missing_image_shot_ids])
    force_extra_videos = len([s for s in force if s not in inv.missing_video_shot_ids])
    est = estimate_missing_cost_usd(
        missing_images=len(inv.missing_image_shot_ids) + force_extra_images,
        missing_videos=len(inv.missing_video_shot_ids) + force_extra_videos,
        missing_tts=len(inv.missing_tts_scene_ids),
        missing_sfx=len(inv.missing_sfx_scene_ids),
        missing_music=len(inv.missing_music_scene_ids),
    )
    inv.estimated_cost_usd = est

    result: dict[str, Any] = {
        "project_id": project_id,
        "estimated_cost_usd": est,
        "report": report,
        "missing_image_shot_ids": image_targets,
        "missing_video_shot_ids": video_targets,
        "missing_tts_scene_ids": inv.missing_tts_scene_ids,
        "missing_sfx_scene_ids": inv.missing_sfx_scene_ids,
        "missing_music_scene_ids": inv.missing_music_scene_ids,
        "force_shot_ids": sorted(force),
        "job_ids": [],
        "assembled": False,
        "started": False,
        "message": "Cost estimate only — pass --confirm or CONFIRM_RESUME=true to run",
    }

    if not confirmation_granted(confirm_flag=confirm):
        return result

    # Unstick orphaned generating/running jobs (e.g. worker restart) before re-dispatch.
    reclaimed = reclaim_in_progress_jobs(project_id)
    result["reclaimedJobIds"] = reclaimed
    if reclaimed:
        db = get_sync_db_from_settings()
        reclaim_docs = [
            j
            for jid in reclaimed
            if (j := job_store.get_job(db, jid)) is not None
        ]
        if reclaim_docs:
            dispatch_jobs(reclaim_docs)

    shot_by_id = {str(s["id"]): s for s in inv.shots}
    image_shots = [shot_by_id[sid] for sid in image_targets if sid in shot_by_id]
    video_shots = [shot_by_id[sid] for sid in video_targets if sid in shot_by_id]

    job_ids: list[str] = []
    # Explicit force gets a regen token so idempotency creates fresh jobs.
    force_token = str(uuid.uuid4()) if force else None

    if image_shots:
        if force:
            forced = [s for s in image_shots if str(s["id"]) in force]
            plain = [s for s in image_shots if str(s["id"]) not in force]
            if plain:
                job_ids.extend(dispatch_image_jobs(project_id, plain, regen_token=None))
            if forced:
                job_ids.extend(
                    dispatch_image_jobs(project_id, forced, regen_token=force_token)
                )
        else:
            job_ids.extend(dispatch_image_jobs(project_id, image_shots, regen_token=None))

    if video_shots:
        if force:
            forced = [s for s in video_shots if str(s["id"]) in force]
            plain = [s for s in video_shots if str(s["id"]) not in force]
            if plain:
                job_ids.extend(
                    dispatch_shot_video_jobs(project_id, plain, regen_token=None)
                )
            if forced:
                job_ids.extend(
                    dispatch_shot_video_jobs(
                        project_id, forced, regen_token=force_token
                    )
                )
        else:
            job_ids.extend(
                dispatch_shot_video_jobs(project_id, video_shots, regen_token=None)
            )

    audio_scene_ids = set(
        inv.missing_tts_scene_ids + inv.missing_sfx_scene_ids
    )
    need_theme = bool(inv.missing_music_scene_ids)
    if audio_scene_ids or need_theme:
        from app.workers.dispatch import dispatch_audio_jobs

        scenes = [s for s in inv.scenes if s.get("id") in audio_scene_ids]
        if need_theme and not scenes:
            # Theme-only: still call dispatch with all scenes so theme duration is correct,
            # but only music job will be new (tts/sfx already exist → idempotent hit).
            scenes = list(inv.scenes)
        if scenes:
            from app.workers.project_context import get_project_doc

            project = get_project_doc(project_id) or {}
            job_ids.extend(
                dispatch_audio_jobs(
                    project_id,
                    scenes if audio_scene_ids else list(inv.scenes),
                    language=str(project.get("language") or "Hindi"),
                    genre=project.get("genre"),
                )
            )

    # Wait for generative jobs when eager; otherwise assembly may race — callers
    # that use Celery async should re-invoke with confirm after jobs finish.
    settings = get_settings()
    if job_ids and settings.celery_task_always_eager:
        db = get_sync_db_from_settings()
        if not job_store.jobs_all_terminal(db, job_ids):
            result["message"] = "Missing-asset jobs still running"
            result["job_ids"] = job_ids
            result["started"] = True
            return result

    # Refresh inventory — assemble when shots + VO/music ready.
    # SFX is optional (ffmpeg mixes without it); never block final cut on fal SFX hangs.
    inv2 = build_inventory(project_id)
    shots_ready = all(r.image_exists and r.clip_exists for r in inv2.rows)
    required_audio_ready = (
        not inv2.missing_tts_scene_ids and not inv2.missing_music_scene_ids
    )
    if assemble and shots_ready and required_audio_ready:
        assemble_token = str(uuid.uuid4())
        asm_ids = dispatch_assembly_jobs(
            project_id,
            scenes=inv2.scenes,
            regenerate_scene_ids=[s["id"] for s in inv2.scenes],
            regen_token=assemble_token,
        )
        job_ids.extend(asm_ids)
        result["assembled"] = True
        skipped_sfx = inv2.missing_sfx_scene_ids
        result["message"] = (
            "Missing assets dispatched (if any); scene_render + final_assembly forced"
            + (
                f" (SFX optional, still missing: {skipped_sfx})"
                if skipped_sfx
                else ""
            )
        )
    elif assemble and not shots_ready:
        result["message"] = (
            "Dispatched missing generative jobs; re-run with --confirm after they "
            "complete to stitch scene_render + final_assembly"
        )
    else:
        result["message"] = "Missing-asset jobs dispatched"

    result["job_ids"] = job_ids
    result["started"] = True
    result["estimated_cost_usd"] = est
    result["report"] = format_inventory_table(inv2 if assemble else inv)
    return result
