"""Download assets, run FFmpeg, upload outputs (worker-safe)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from bson import ObjectId

from app.media.audio_timeline import (
    assign_lines_to_shots,
    build_shot_linked_voice_timeline,
    build_shot_windows,
    build_voice_timeline,
    dialogue_lines_from_scene,
)
from app.media.ffmpeg_service import (
    FFmpegValidationError,
    SceneInputs,
    VoiceLineInput,
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


def missing_planned_shot_ids(
    planned: list[str],
    produced: list[str] | set[str],
) -> list[str]:
    """Return planned shot ids that have no produced clip (stable order)."""
    have = {str(x) for x in produced}
    return [str(sid) for sid in planned if str(sid) not in have]


def assert_planned_shots_have_clips(
    *,
    scene_id: str,
    planned_shot_ids: list[str],
    produced_shot_ids: list[str] | set[str],
) -> None:
    """Fail loudly before assembly when any planned shot lacks a clip."""
    missing = missing_planned_shot_ids(planned_shot_ids, produced_shot_ids)
    if not missing:
        return
    raise ValueError(
        f"Missing video clips for {len(missing)} planned shot(s) in {scene_id}: "
        f"{', '.join(missing)}. "
        f"Planned={len(planned_shot_ids)} produced={len(set(map(str, produced_shot_ids)))}. "
        "Every planned shot must have its own image and video clip before assembly."
    )


def _scene_assets(project_id: str, scene_id: str) -> dict[str, Any]:
    db = _db()
    assets = list_assets_for_project(db, project_id)
    scene_assets = [a for a in assets if a.get("scene_id") == scene_id]
    ok_video = {
        str(j["id"])
        for j in job_store.list_jobs_for_project(db, project_id)
        if j.get("type") == "video"
        and j.get("status") in job_store.SUCCESS_STATUSES
    }
    ok_tts = {
        str(j["id"])
        for j in job_store.list_jobs_for_project(db, project_id)
        if j.get("type") == "tts"
        and j.get("status") in job_store.SUCCESS_STATUSES
    }
    # Latest successful clip per shot_id (skip assets from failed jobs).
    by_shot: dict[str, dict[str, Any]] = {}
    for asset in scene_assets:
        if asset.get("type") != "video" or not asset.get("shot_id"):
            continue
        jid = str(asset.get("job_id") or "")
        if jid and jid not in ok_video:
            continue
        by_shot[str(asset["shot_id"])] = asset
    shots = sorted(by_shot.values(), key=lambda a: str(a.get("shot_id")))

    def _latest(asset_type: str) -> dict[str, Any] | None:
        matches = [a for a in scene_assets if a.get("type") == asset_type]
        return matches[-1] if matches else None

    # Per-line TTS assets for this scene (shot_id = line_id), script order.
    tts_by_line: dict[str, dict[str, Any]] = {}
    for asset in scene_assets:
        if asset.get("type") != "tts":
            continue
        jid = str(asset.get("job_id") or "")
        if jid and jid not in ok_tts:
            continue
        meta = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        line_id = str(
            meta.get("line_id") or asset.get("shot_id") or asset.get("id") or ""
        )
        if not line_id:
            continue
        tts_by_line[line_id] = asset

    def _line_sort_key(asset: dict[str, Any]) -> tuple[int, str]:
        meta = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        try:
            idx = int(meta.get("line_index"))
        except (TypeError, ValueError):
            idx = 10_000
        return (idx, str(meta.get("line_id") or asset.get("shot_id") or ""))

    tts_lines = sorted(tts_by_line.values(), key=_line_sort_key)
    # Legacy fallback: single scene-level TTS without line id.
    if not tts_lines:
        legacy = _latest("tts")
        if legacy:
            tts_lines = [legacy]

    # Project-level theme music (no scene_id); fall back to any music asset.
    theme_matches = [
        a for a in assets if a.get("type") == "music" and not a.get("scene_id")
    ]
    if not theme_matches:
        theme_matches = [a for a in assets if a.get("type") == "music"]
    music = theme_matches[-1] if theme_matches else None

    return {
        "shots": shots,
        "by_shot": by_shot,
        "tts_lines": tts_lines,
        "tts": tts_lines[-1] if tts_lines else None,
        "sfx": _latest("sfx"),
        "music": music,
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

    # Compare planned shots vs produced clips — fail before FFmpeg stitch.
    from app.workers.project_context import planned_shot_ids_for_scene

    planned = planned_shot_ids_for_scene(job["project_id"], str(scene_id))
    produced_ids = list(bundle.get("by_shot") or {})
    if planned:
        assert_planned_shots_have_clips(
            scene_id=str(scene_id),
            planned_shot_ids=planned,
            produced_shot_ids=produced_ids,
        )
        # Stitch in planned order when available.
        ordered_assets = [
            bundle["by_shot"][sid] for sid in planned if sid in bundle["by_shot"]
        ]
    else:
        ordered_assets = list(bundle["shots"])

    inp = job.get("input") or {}
    scene_plan = float(inp.get("duration_seconds") or 0) or None

    tmp_root = Path(tempfile.mkdtemp(prefix=f"scene-{scene_id[:8]}-"))
    try:
        shot_paths: list[Path] = []
        shot_plans: list[float] = []
        default_each = (
            (scene_plan / len(ordered_assets)) if scene_plan else 5.0
        )
        for index, asset in enumerate(ordered_assets):
            shot_paths.append(
                _download_asset(storage, asset, tmp_root / f"shot_{index}.mp4")
            )
            shot_plans.append(_shot_plan_seconds(asset, default_each))

        # Scene plan wins; else sum of per-shot plans (covers multi-shot scenes).
        if scene_plan is None or scene_plan <= 0:
            scene_plan = sum(shot_plans)

        # Download per-line TTS and place each line on its linked shot window.
        gap = float(getattr(settings, "audio_line_gap_seconds", 0.3) or 0.3)
        line_durs: list[tuple[str, float]] = []
        line_paths: dict[str, Path] = {}
        linked_from_meta: dict[str, str] = {}
        for index, asset in enumerate(bundle.get("tts_lines") or []):
            meta = (
                asset.get("metadata")
                if isinstance(asset.get("metadata"), dict)
                else {}
            )
            line_id = str(
                meta.get("line_id") or asset.get("shot_id") or f"line-{index + 1}"
            )
            path = _download_asset(
                storage, asset, tmp_root / f"voice_{index}_{line_id}.wav"
            )
            line_paths[line_id] = path
            dur = float(asset.get("duration") or meta.get("duration_seconds") or 0.0)
            if dur <= 0:
                from app.media.ffprobe import probe_file

                dur = float(probe_file(path).get("duration") or 0.01)
            line_durs.append((line_id, dur))
            linked = str(meta.get("linked_shot_id") or "").strip()
            if linked:
                linked_from_meta[line_id] = linked

        # Fall back to director plan for older TTS assets without linked_shot_id.
        if planned and (len(linked_from_meta) < len(line_durs)):
            from app.workers.project_context import get_project_doc

            doc = get_project_doc(job["project_id"]) or {}
            director = doc.get("director_response") or doc.get("directorResponse") or {}
            scene_doc = next(
                (
                    sc
                    for sc in (director.get("scenes") or [])
                    if isinstance(sc, dict) and str(sc.get("id")) == str(scene_id)
                ),
                None,
            )
            explicit = dict(linked_from_meta)
            if scene_doc:
                for ln in dialogue_lines_from_scene(scene_doc):
                    if ln.get("shot_id") and ln["id"] not in explicit:
                        explicit[str(ln["id"])] = str(ln["shot_id"])
            line_shot_map = assign_lines_to_shots(
                [lid for lid, _ in line_durs],
                list(planned),
                explicit=explicit,
            )
        elif planned:
            line_shot_map = assign_lines_to_shots(
                [lid for lid, _ in line_durs],
                list(planned),
                explicit=linked_from_meta,
            )
        else:
            line_shot_map = dict(linked_from_meta)

        shot_duration_pairs = [
            (sid, float(shot_plans[i] if i < len(shot_plans) else 5.0))
            for i, sid in enumerate(planned)
        ] if planned else []
        if not shot_duration_pairs and ordered_assets:
            shot_duration_pairs = [
                (str(a.get("shot_id") or f"shot-{i+1}"), float(shot_plans[i]))
                for i, a in enumerate(ordered_assets)
            ]

        if shot_duration_pairs and line_durs:
            windows = build_shot_windows(shot_duration_pairs)
            timeline = build_shot_linked_voice_timeline(
                line_durs,
                shot_windows=windows,
                line_shot_map=line_shot_map,
                gap_seconds=gap,
            )
        else:
            timeline = build_voice_timeline(
                line_durs,
                scene_duration_seconds=float(scene_plan or 0.0),
                gap_seconds=gap,
            )
        voice_inputs = [
            VoiceLineInput(
                path=line_paths[p.line_id],
                start_seconds=p.start_seconds,
                duration_seconds=p.duration_seconds,
                line_id=p.line_id,
            )
            for p in timeline.placements
            if p.line_id in line_paths
        ]

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
        work = tmp_root / "work"
        duration = render_scene_video(
            work_dir=work,
            inputs=SceneInputs(
                shot_clips=shot_paths,
                voice_lines=voice_inputs,
                sfx=sfx,
                music=music,
                target_duration=float(scene_plan),
                shot_target_durations=shot_plans,
                music_volume=float(
                    getattr(settings, "audio_music_volume", 0.30) or 0.30
                ),
                sfx_volume=float(getattr(settings, "audio_sfx_volume", 0.22) or 0.22),
                voice_volume=float(
                    getattr(settings, "audio_voice_volume", 1.0) or 1.0
                ),
                loudnorm_i=float(
                    getattr(settings, "audio_loudnorm_i", -16.0) or -16.0
                ),
            ),
            output_path=out,
            width=settings.ffmpeg_width,
            height=settings.ffmpeg_height,
            fps=settings.ffmpeg_fps,
        )
        mismatch_meta: dict[str, Any] = {}
        overflow_meta: dict[str, Any] = {}
        flag_file = work / "shot_duration_mismatch.json"
        overflow_file = work / "voice_overflow.json"
        if flag_file.is_file():
            try:
                import json

                mismatch_meta = json.loads(flag_file.read_text(encoding="utf-8"))
            except Exception:
                mismatch_meta = {}
        if overflow_file.is_file():
            try:
                import json

                overflow_meta = json.loads(overflow_file.read_text(encoding="utf-8"))
            except Exception:
                overflow_meta = {}
        if timeline.overflow or overflow_meta.get("overflow"):
            import logging

            logging.getLogger(__name__).warning(
                "VOICE_OVERFLOW scene=%s voice_end=%.3fs scene_plan=%.3fs "
                "(lines were NOT cut; mix extended)",
                scene_id,
                timeline.total_voice_seconds,
                float(scene_plan or 0.0),
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
                "shot_duration_mismatch": bool(mismatch_meta.get("any")),
                "shot_duration_mismatches": mismatch_meta.get("mismatches") or [],
                "voice_line_count": len(voice_inputs),
                "voice_timeline": [
                    {
                        "line_id": p.line_id,
                        "shot_id": p.shot_id,
                        "start": p.start_seconds,
                        "duration": p.duration_seconds,
                    }
                    for p in timeline.placements
                ],
                "voice_shot_map": {
                    p.line_id: p.shot_id
                    for p in timeline.placements
                    if p.shot_id
                },
                "voice_overflow": bool(
                    timeline.overflow or overflow_meta.get("overflow")
                ),
                "voice_overflow_seconds": float(
                    timeline.overflow_seconds
                    or overflow_meta.get("overflow_seconds")
                    or 0.0
                ),
                "voice_shot_overflows": list(timeline.shot_overflows or []),
            },
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def assemble_final_for_job(job: dict[str, Any]) -> dict[str, Any]:
    settings = get_worker_settings()
    storage = get_storage()
    db = _db()
    inp = job.get("input") or {}
    # Never stitch while video clips are still running. Prior failed attempts are
    # OK when a later attempt for the same shot succeeded (checked via assets).
    video_jobs = [
        j
        for j in job_store.list_jobs_for_project(db, job["project_id"])
        if j.get("type") == "video"
    ]
    if video_jobs:
        incomplete = [
            j
            for j in video_jobs
            if j.get("status") not in job_store.TERMINAL_STATUSES
        ]
        if incomplete:
            raise ValueError(
                f"Cannot assemble final: {len(incomplete)} video clip(s) "
                "still running (regenerate failed clips only, then retry stitch)"
            )
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
        crossfade = float(
            getattr(settings, "audio_scene_crossfade_seconds", 0.4) or 0.4
        )
        if len(scene_paths) > 1 and crossfade > 0:
            expected_duration = max(
                0.1,
                expected_duration - crossfade * (len(scene_paths) - 1),
            )
        duration = assemble_final_video(
            scene_videos=scene_paths,
            output_path=out,
            audio_crossfade_seconds=crossfade,
        )
        validate_final_output(
            out,
            expected_duration=expected_duration or duration,
            tolerance_seconds=max(
                settings.ffmpeg_duration_tolerance_seconds, crossfade + 1.0
            ),
        )
        data = out.read_bytes()
        result = _upload_video_output(
            job=job,
            data=data,
            asset_type="final_assembly",
            filename="final.mp4",
            duration=duration,
            metadata={
                "scene_ids": scene_ids,
                "ffmpeg": True,
                "validated": True,
                "audio_crossfade_seconds": crossfade,
            },
        )
        _mark_project_completed(job["project_id"], result["asset_id"], result["r2_key"])
        return result
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _mark_project_completed(project_id: str, asset_id: str, r2_key: str) -> None:
    settings = get_worker_settings()
    db = _db()
    if not settings.allow_mock:
        mock_assets = [
            a
            for a in list_assets_for_project(db, project_id)
            if str(a.get("provider") or "").lower() == "mock"
        ]
        if mock_assets:
            raise RuntimeError(
                f"Refusing to mark project {project_id} completed: "
                f"{len(mock_assets)} mock asset(s) present (ALLOW_MOCK=false)"
            )
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
