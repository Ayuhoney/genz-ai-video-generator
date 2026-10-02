"""Download assets, run FFmpeg, upload outputs (worker-safe)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from bson import ObjectId

from app.media.ffmpeg_service import (
    FFmpegValidationError,
    SceneInputs,
    assemble_final_video,
    render_scene_video,
    validate_final_output,
)
from app.media.ffprobe import probe_file
from app.storage import build_r2_key, get_storage
from app.storage.asset_store import create_asset, list_assets_for_project
from app.workers import job_store
from app.workers.settings import get_worker_settings


def _db():
    settings = get_worker_settings()
    return job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)


def _download_asset(storage, asset: dict[str, Any], dest: Path) -> Path:
    data = storage.download(asset["r2_key"])
    dest.write_bytes(data)
    return dest


def _scene_assets(project_id: str, scene_id: str) -> dict[str, Any]:
    db = _db()
    assets = list_assets_for_project(db, project_id)
    scene_assets = [a for a in assets if a.get("scene_id") == scene_id]
    shots = sorted(
        [a for a in scene_assets if a.get("type") == "video" and a.get("shot_id")],
        key=lambda a: str(a.get("shot_id")),
    )
    def _latest(asset_type: str) -> dict[str, Any] | None:
        matches = [a for a in scene_assets if a.get("type") == asset_type]
        return matches[-1] if matches else None

    return {
        "shots": shots,
        "tts": _latest("tts"),
        "sfx": _latest("sfx"),
        "music": _latest("music"),
    }


def _upload_video_output(
    *,
    job: dict[str, Any],
    data: bytes,
    asset_type: str,
    filename: str,
    duration: float,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    storage = get_storage()
    db = _db()
    key = build_r2_key(
        project_id=job["project_id"],
        scene_id=job.get("scene_id"),
        shot_id=job.get("shot_id"),
        asset_type=asset_type,
        filename=f"{job['id']}_{filename}",
    )
    storage.upload(key, data, content_type="video/mp4")
    asset = create_asset(
        db,
        project_id=job["project_id"],
        scene_id=job.get("scene_id"),
        shot_id=job.get("shot_id"),
        asset_type=asset_type,
        r2_key=key,
        mime="video/mp4",
        size=len(data),
        provider="ffmpeg",
        cost=0.0,
        duration=duration,
        job_id=job["id"],
        metadata=metadata,
    )
    job_store.update_job(
        db,
        job["id"],
        cost=0.0,
        asset_id=asset["id"],
        r2_key=key,
    )
    return {
        "type": asset_type,
        "output_key": key,
        "r2_key": key,
        "asset_id": asset["id"],
        "mime": "video/mp4",
        "size": len(data),
        "provider": "ffmpeg",
        "duration": duration,
        "tmp_cleaned": True,
    }


def _shot_plan_seconds(asset: dict[str, Any], fallback: float) -> float:
    """Planned shot length from metadata — never use probed fal clip length."""
    meta = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    for key in (
        "planned_duration_seconds",
        "requested_duration_seconds",
        "requested_duration",
        "plan_duration_seconds",
        "duration_seconds",
    ):
        raw = meta.get(key)
        try:
            value = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return max(0.1, float(fallback))


def render_scene_for_job(job: dict[str, Any]) -> dict[str, Any]:
    scene_id = job.get("scene_id")
    if not scene_id:
        raise ValueError("scene_render job requires scene_id")
    settings = get_worker_settings()
    storage = get_storage()
    bundle = _scene_assets(job["project_id"], scene_id)
    if not bundle["shots"]:
        raise ValueError(f"No shot videos found for scene {scene_id}")

    inp = job.get("input") or {}
    scene_plan = float(inp.get("duration_seconds") or 0) or None

    tmp_root = Path(tempfile.mkdtemp(prefix=f"scene-{scene_id[:8]}-"))
    try:
        shot_paths: list[Path] = []
        shot_plans: list[float] = []
        default_each = (
            (scene_plan / len(bundle["shots"])) if scene_plan else 5.0
        )
        for index, asset in enumerate(bundle["shots"]):
            shot_paths.append(
                _download_asset(storage, asset, tmp_root / f"shot_{index}.mp4")
            )
            shot_plans.append(_shot_plan_seconds(asset, default_each))

        # Scene plan wins; else sum of per-shot plans (covers multi-shot scenes).
        if scene_plan is None or scene_plan <= 0:
            scene_plan = sum(shot_plans)

        narration = (
            _download_asset(storage, bundle["tts"], tmp_root / "narration.mp3")
            if bundle["tts"]
            else None
        )
        sfx = (
            _download_asset(storage, bundle["sfx"], tmp_root / "sfx.mp3")
            if bundle["sfx"]
            else None
        )
        music = (
            _download_asset(storage, bundle["music"], tmp_root / "music.mp3")
            if bundle["music"]
            else None
        )
        out = tmp_root / "scene.mp4"
        duration = render_scene_video(
            work_dir=tmp_root / "work",
            inputs=SceneInputs(
                shot_clips=shot_paths,
                narration=narration,
                sfx=sfx,
                music=music,
                target_duration=float(scene_plan),
                shot_target_durations=shot_plans,
            ),
            output_path=out,
            width=settings.ffmpeg_width,
            height=settings.ffmpeg_height,
            fps=settings.ffmpeg_fps,
        )
        data = out.read_bytes()
        return _upload_video_output(
            job=job,
            data=data,
            asset_type="scene_render",
            filename="scene.mp4",
            duration=duration,
            metadata={
                "scene_id": scene_id,
                "ffmpeg": True,
                "planned_duration_seconds": float(scene_plan),
                "shot_planned_durations": shot_plans,
            },
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def assemble_final_for_job(job: dict[str, Any]) -> dict[str, Any]:
    settings = get_worker_settings()
    storage = get_storage()
    db = _db()
    inp = job.get("input") or {}
    scene_ids = list(inp.get("scene_ids") or [])
    if not scene_ids:
        # Fallback: all scene_render assets ordered by scene id.
        assets = list_assets_for_project(db, job["project_id"])
        scene_ids = sorted(
            {
                a["scene_id"]
                for a in assets
                if a.get("type") == "scene_render" and a.get("scene_id")
            }
        )
    if not scene_ids:
        raise ValueError("No scene renders available for final assembly")

    tmp_root = Path(tempfile.mkdtemp(prefix="final-asm-"))
    try:
        scene_paths: list[Path] = []
        expected_duration = 0.0
        for scene_id in scene_ids:
            assets = list_assets_for_project(db, job["project_id"])
            matches = [
                a
                for a in assets
                if a.get("scene_id") == scene_id and a.get("type") == "scene_render"
            ]
            if not matches:
                raise ValueError(f"Missing scene_render asset for {scene_id}")
            asset = matches[-1]
            path = _download_asset(storage, asset, tmp_root / f"{scene_id}.mp4")
            scene_paths.append(path)
            expected_duration += float(asset.get("duration") or 0.0)

        out = tmp_root / "final.mp4"
        duration = assemble_final_video(scene_videos=scene_paths, output_path=out)
        validate_final_output(
            out,
            expected_duration=expected_duration or duration,
            tolerance_seconds=settings.ffmpeg_duration_tolerance_seconds,
        )
        data = out.read_bytes()
        result = _upload_video_output(
            job=job,
            data=data,
            asset_type="final_assembly",
            filename="final.mp4",
            duration=duration,
            metadata={"scene_ids": scene_ids, "ffmpeg": True, "validated": True},
        )
        _mark_project_completed(job["project_id"], result["asset_id"], result["r2_key"])
        return result
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _mark_project_completed(project_id: str, asset_id: str, r2_key: str) -> None:
    db = _db()
    filt: list[dict[str, Any]] = [{"_id": project_id}]
    if ObjectId.is_valid(project_id):
        filt.append({"_id": ObjectId(project_id)})
    db.projects.update_one(
        {"$or": filt},
        {
            "$set": {
                "status": "completed",
                "final_video_asset_id": asset_id,
                "final_video_r2_key": r2_key,
            }
        },
    )
