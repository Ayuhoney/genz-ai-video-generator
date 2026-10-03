"""Generate via provider registry, upload to storage, record asset metadata."""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from bson import ObjectId

from app.providers.base import (
    BudgetExceededError,
    ImageRequest,
    MusicRequest,
    ProviderResult,
    SFXRequest,
    TTSRequest,
    VideoRequest,
)
from app.providers.registry import get_registry
from app.storage import build_r2_key, get_storage
from app.storage.asset_store import (
    create_asset,
    get_sync_db_from_settings,
    list_assets_for_project,
    sum_cost_for_project,
)
from app.media.ffprobe import probe_duration_seconds, suffix_for_mime
from app.providers.sarvam.audio import language_to_sarvam_code
from app.providers.fal.clip_timing import clamp_clip_seconds
from app.workers import job_store
from app.workers.project_context import (
    build_music_prompt,
    build_sfx_prompt,
    character_descriptions_prompt,
    fallback_spoken_line,
    first_scene_id,
    get_locked_look,
    get_project_doc,
    locale_visual_lock,
    locked_character_refs,
    scene_script_from_graph,
    set_locked_look,
)

logger = logging.getLogger(__name__)

AssetKind = Literal[
    "image",
    "video",
    "tts",
    "sfx",
    "music",
    "ffmpeg",
    "scene_render",
    "final_assembly",
]


def _settings():
    try:
        from app.core.config import get_settings

        return get_settings()
    except Exception:
        from app.workers.settings import get_worker_settings

        return get_worker_settings()


def _reuse_existing_asset(
    *,
    project_id: str,
    asset_type: str,
    scene_id: str | None,
    shot_id: str | None,
    force: bool = False,
) -> dict[str, Any] | None:
    """Reuse latest successful on-disk asset for this scene/shot (skip fal)."""
    if force:
        return None
    from app.workers.resume_missing import latest_successful_asset

    db = get_sync_db_from_settings()
    assets = list_assets_for_project(db, project_id)
    ok = {
        str(j["id"])
        for j in job_store.list_jobs_for_project(db, project_id)
        if j.get("type") == asset_type and j.get("status") in job_store.SUCCESS_STATUSES
    }
    asset = latest_successful_asset(
        assets,
        asset_type=asset_type,
        ok_job_ids=ok,
        scene_id=scene_id,
        shot_id=shot_id,
        # Never reuse a scene-level still for another shot.
        allow_scene_image_fallback=False,
    )
    if not asset:
        return None
    key = str(asset.get("r2_key") or "")
    return {
        "type": asset_type,
        "reused": True,
        "output_key": key,
        "r2_key": key,
        "asset_id": asset.get("id"),
        "mime": asset.get("mime"),
        "size": asset.get("size"),
        "cost": 0.0,
        "provider": "reuse",
        "duration": asset.get("duration"),
        "tmp_cleaned": True,
    }


def set_project_paused_budget(db: Any, project_id: str) -> None:
    """Mark project status paused_budget (sync Mongo)."""
    now_filter: list[dict[str, Any]] = [{"_id": project_id}]
    if ObjectId.is_valid(project_id):
        now_filter.append({"_id": ObjectId(project_id)})
    db.projects.update_one(
        {"$or": now_filter},
        {"$set": {"status": "paused_budget"}},
    )


def enforce_budget(project_id: str) -> None:
    settings = _settings()
    limit = float(settings.max_project_cost_usd or 0.0)
    if limit <= 0:
        return
    db = get_sync_db_from_settings()
    spent = sum_cost_for_project(db, project_id)
    if spent >= limit:
        set_project_paused_budget(db, project_id)
        raise BudgetExceededError(project_id, spent, limit)


def _record_and_upload(
    *,
    job: dict[str, Any],
    result: ProviderResult,
    asset_type: str,
) -> dict[str, Any]:
    enforce_budget(job["project_id"])
    storage = get_storage()
    db = get_sync_db_from_settings()
    data = result.require_bytes()
    if asset_type in {"tts", "sfx", "music", "video"}:
        probed = probe_duration_seconds(
            data,
            suffix=suffix_for_mime(result.mime_type),
        )
        if probed is not None:
            if asset_type == "video":
                planned = float(result.duration_seconds or 0.0)
                result.metadata = {
                    **result.metadata,
                    "duration_ffprobe": probed,
                    "actual_duration_seconds": probed,
                    "planned_duration_seconds": planned or probed,
                    "requested_duration_seconds": planned or probed,
                }
                # Keep planned length on the asset for scene timing; actual is in metadata.
            else:
                result.duration_seconds = probed
                result.metadata = {**result.metadata, "duration_ffprobe": probed}
    filename = f"{job['id']}_{result.filename}"
    key = build_r2_key(
        project_id=job["project_id"],
        scene_id=job.get("scene_id"),
        shot_id=job.get("shot_id"),
        asset_type=asset_type,
        filename=filename,
    )
    storage.upload(key, data, content_type=result.mime_type)
    asset = create_asset(
        db,
        project_id=job["project_id"],
        scene_id=job.get("scene_id"),
        shot_id=job.get("shot_id"),
        asset_type=asset_type,
        r2_key=key,
        mime=result.mime_type,
        size=len(data),
        provider=result.provider_name,
        cost=result.cost_usd,
        duration=result.duration_seconds,
        job_id=job["id"],
        metadata=result.metadata,
    )
    job_store.update_job(
        db,
        job["id"],
        cost=result.cost_usd,
        asset_id=asset["id"],
        r2_key=key,
    )
    # Re-check after recording cost
    spent = sum_cost_for_project(db, job["project_id"])
    limit = float(_settings().max_project_cost_usd or 0.0)
    if limit > 0 and spent >= limit:
        set_project_paused_budget(db, job["project_id"])

    return {
        "type": asset_type,
        "output_key": key,
        "r2_key": key,
        "asset_id": asset["id"],
        "mime": result.mime_type,
        "size": len(data),
        "provider": result.provider_name,
        "cost": result.cost_usd,
        "duration": result.duration_seconds,
        "tmp_cleaned": True,
    }


def _scene_media_for_type(
    project_id: str,
    scene_id: str | None,
    asset_type: str,
) -> tuple[bytes | None, str | None, str | None]:
    """Return (bytes, mime, http_url) for latest scene asset of a type."""
    if not scene_id:
        return None, None, None
    db = get_sync_db_from_settings()
    assets = list_assets_for_project(db, project_id)
    matches = [
        a
        for a in assets
        if a.get("type") == asset_type and a.get("scene_id") == scene_id
    ]
    if not matches:
        return None, None, None
    asset = matches[-1]
    storage = get_storage()
    data = storage.download(asset["r2_key"])
    mime = str(asset.get("mime") or "application/octet-stream")
    url: str | None = None
    settings = _settings()
    public = (getattr(settings, "r2_public_base_url", None) or "").rstrip("/")
    if public:
        url = f"{public}/{asset['r2_key']}"
    else:
        try:
            signed = storage.presigned_url(asset["r2_key"], expires_in=3600)
            if signed.startswith("http://") or signed.startswith("https://"):
                url = signed
        except Exception:
            url = None
    return data, mime, url


def _shot_media_for_type(
    project_id: str,
    scene_id: str | None,
    shot_id: str | None,
    asset_type: str,
) -> tuple[bytes | None, str | None, str | None]:
    """Return (bytes, mime, http_url) for latest shot asset of a type."""
    if not shot_id:
        return None, None, None
    db = get_sync_db_from_settings()
    assets = list_assets_for_project(db, project_id)
    matches = [
        a
        for a in assets
        if a.get("type") == asset_type
        and a.get("shot_id") == shot_id
        and (scene_id is None or a.get("scene_id") == scene_id)
    ]
    if not matches:
        return None, None, None
    asset = matches[-1]
    storage = get_storage()
    data = storage.download(asset["r2_key"])
    mime = str(asset.get("mime") or "application/octet-stream")
    url: str | None = None
    settings = _settings()
    public = (getattr(settings, "r2_public_base_url", None) or "").rstrip("/")
    if public:
        url = f"{public}/{asset['r2_key']}"
    else:
        try:
            signed = storage.presigned_url(asset["r2_key"], expires_in=3600)
            if signed.startswith("http://") or signed.startswith("https://"):
                url = signed
        except Exception:
            url = None
    return data, mime, url


def _shot_image_for_i2v(
    project_id: str,
    scene_id: str | None,
    shot_id: str | None,
) -> tuple[bytes | None, str | None, str | None]:
    """Shot still for i2v; falls back to scene-level still so resume can reuse."""
    data, mime, url = _shot_media_for_type(project_id, scene_id, shot_id, "image")
    if data or url:
        return data, mime, url
    return _scene_media_for_type(project_id, scene_id, "image")


def _scene_image_for_i2v(
    project_id: str,
    scene_id: str | None,
) -> tuple[bytes | None, str | None, str | None]:
    """Legacy helper: latest scene-level still (no shot_id). Prefer _shot_image_for_i2v."""
    return _scene_media_for_type(project_id, scene_id, "image")


def _scene_visual_description(project_id: str, scene_id: str | None) -> str:
    """Visual English description for image gen — not TTS narration."""
    if not scene_id:
        return ""
    doc = get_project_doc(project_id) or {}
    director = doc.get("director_response") or doc.get("directorResponse") or {}
    if isinstance(director, dict):
        for sc in director.get("scenes") or []:
            if not isinstance(sc, dict):
                continue
            if str(sc.get("id") or "") != str(scene_id):
                continue
            return str(sc.get("description") or sc.get("title") or "").strip()
    try:
        from app.orchestration.checkpointing import thread_config
        from app.orchestration.graph import compile_graph

        graph = compile_graph()
        snapshot = graph.get_state(thread_config(project_id))
        for scene in (snapshot.values or {}).get("scenes") or []:
            if scene.get("id") == scene_id:
                return str(scene.get("description") or scene.get("title") or "").strip()
    except Exception:
        pass
    return ""


def _shot_visual_description(
    project_id: str,
    *,
    scene_id: str | None,
    shot_id: str | None,
    inp: dict[str, Any] | None = None,
) -> str:
    """Shot title + description + camera + characters present — not scene layout text."""
    inp = inp or {}
    title = str(inp.get("title") or "").strip()
    description = str(inp.get("description") or "").strip()
    camera = str(inp.get("camera") or "").strip()
    prompt = str(inp.get("prompt") or "").strip()
    if not (title or description or camera):
        # Hydrate from director / graph shot plan.
        try:
            from app.workers.project_context import load_director_plan_for_production

            loaded = load_director_plan_for_production(project_id)
            if loaded:
                _plan, _scenes, shots = loaded
                for sh in shots:
                    if shot_id and str(sh.get("id") or "") == str(shot_id):
                        title = str(sh.get("title") or title).strip()
                        description = str(sh.get("description") or description).strip()
                        camera = str(sh.get("camera") or camera).strip()
                        break
        except Exception:
            pass
    parts = [p for p in (title, description, camera) if p]
    visual = ". ".join(parts).strip() or prompt
    if visual.lower() == "storyboard":
        visual = ""
    cast = character_descriptions_prompt(
        project_id,
        shot={
            "title": title,
            "description": description,
            "camera": camera,
            "prompt": visual,
            "scene_id": scene_id,
        },
        scene_id=scene_id,
    )
    if cast:
        visual = f"{visual}. {cast}" if visual else cast
    return visual.strip()


def estimate_images_cost_usd(shot_count: int) -> float:
    """images = shots × per-image cost."""
    unit = float(getattr(_settings(), "fal_image_cost_usd", 0.0) or 0.0)
    return round(max(0, int(shot_count)) * unit, 6)


def _upload_look_to_fal_cdn(data: bytes, mime: str | None) -> str:
    """Upload locked look bytes to fal CDN (private storage URLs are rejected)."""
    from app.providers.fal.image_input import ensure_uploaded, validate_image_bytes
    from app.providers.fal.image_video import _make_client

    validated = validate_image_bytes(data, claimed_mime=mime)
    with _make_client() as client:
        return ensure_uploaded(client, validated)


def _wait_for_locked_look(
    project_id: str,
    *,
    expected_scene_id: str,
    timeout_seconds: float,
) -> tuple[bytes, str]:
    """Block until starting-scene still is locked; return (bytes, mime)."""
    deadline = time.time() + max(5.0, float(timeout_seconds or 600.0))
    poll = 2.0
    storage = get_storage()
    while time.time() < deadline:
        lock_scene, r2_key = get_locked_look(project_id)
        if lock_scene == str(expected_scene_id) and r2_key:
            try:
                if storage.exists(r2_key):
                    data = storage.download(r2_key)
                    mime = "image/jpeg"
                    db = get_sync_db_from_settings()
                    for asset in list_assets_for_project(db, project_id):
                        if asset.get("r2_key") == r2_key:
                            mime = str(asset.get("mime") or mime)
                            break
                    if data:
                        return data, mime
            except Exception as exc:
                logger.warning("locked look download failed: %s", exc)
        time.sleep(poll)
    raise RuntimeError(
        f"Timed out waiting for starting-scene face lock ({expected_scene_id})"
    )


def produce_image(job: dict[str, Any]) -> dict[str, Any]:
    enforce_budget(job["project_id"])
    inp = job.get("input") or {}
    force = bool(inp.get("force") or inp.get("regen_token"))
    project_id = job["project_id"]
    scene_id = job.get("scene_id")
    shot_id = job.get("shot_id") or inp.get("shot_id")
    anchor_id = first_scene_id(project_id)
    is_anchor = bool(
        scene_id and anchor_id and str(scene_id) == str(anchor_id)
    )
    # Reserve per-shot image cost for budget accounting.
    est = estimate_images_cost_usd(1)
    if est > 0:
        job_store.update_job(
            get_sync_db_from_settings(),
            job["id"],
            estimated_cost_usd=est,
        )
    reused = _reuse_existing_asset(
        project_id=project_id,
        asset_type="image",
        scene_id=scene_id,
        shot_id=shot_id,
        force=force,
    )
    if reused:
        if is_anchor and reused.get("r2_key"):
            set_locked_look(
                project_id,
                scene_id=str(scene_id),
                r2_key=str(reused["r2_key"]),
            )
        return reused
    # Never use kind="storyboard" (or any kind) as the image prompt.
    visual = _shot_visual_description(
        project_id,
        scene_id=scene_id,
        shot_id=str(shot_id) if shot_id else None,
        inp=inp,
    )
    if not visual or visual.lower() == "storyboard":
        raise ValueError(
            f"Missing shot description for image job "
            f"(project={project_id} scene={scene_id} shot={shot_id}); "
            "cannot use kind as prompt"
        )
    # FalImageProvider wraps with soft_visual_prompt once — do not pre-wrap.
    prompt = visual
    locale = locale_visual_lock(project_id)
    if locale and "LOCALE LOCK" not in prompt:
        prompt = f"{prompt}. {locale}"
    # Only remind continuity for characters actually in this shot — never dump
    # the full cast bible onto CCTV / crowd / B-roll frames.
    if "Characters in frame" in visual and "CHARACTER BIBLE" not in prompt:
        prompt = (
            f"{prompt}. Keep the same Indian identity for named people in frame."
        )
    refs = locked_character_refs(project_id)
    if refs:
        looks = "; ".join(
            f"{r['name']}: {r['description']}" for r in refs if r.get("description")
        )
        prompt = (
            f"{prompt}. FACE LOCK — same identity every shot for: {looks}. "
            "Do not change face, age, or hairstyle. "
            "Use the character reference photo for identity only — "
            "do not copy the reference composition, pose, or background."
        )
    # Look-lock anchor = character reference URLs only (never scene-1 layout).
    ref_urls = [r["url"] for r in refs if r.get("url")]
    extra: dict[str, Any] = {}
    settings = _settings()
    if ref_urls:
        extra["strength"] = float(
            getattr(settings, "fal_image_i2i_strength", 0.65) or 0.65
        )
        i2i = str(getattr(settings, "fal_image_i2i_model", "") or "").strip()
        if i2i:
            extra["i2i_model"] = i2i
        logger.info(
            "look_lock project=%s scene=%s shot=%s character_refs=%d",
            project_id,
            scene_id,
            shot_id,
            len(ref_urls),
        )

    result = get_registry().generate_image(
        ImageRequest(
            project_id=project_id,
            scene_id=scene_id,
            prompt=prompt,
            width=1280,
            height=720,
            reference_image_urls=ref_urls,
            extra=extra,
        )
    )
    out = _record_and_upload(job=job, result=result, asset_type="image")
    if is_anchor and out.get("r2_key"):
        set_locked_look(
            project_id,
            scene_id=str(scene_id),
            r2_key=str(out["r2_key"]),
        )
    return out


def produce_video(job: dict[str, Any]) -> dict[str, Any]:
    """Image-to-video: this shot's still → clip; duration clamped to fal WAN max (~15s)."""
    enforce_budget(job["project_id"])
    inp = job.get("input") or {}
    force = bool(inp.get("force") or inp.get("regen_token"))
    shot_id = job.get("shot_id") or inp.get("shot_id")
    reused = _reuse_existing_asset(
        project_id=job["project_id"],
        asset_type="video",
        scene_id=job.get("scene_id"),
        shot_id=shot_id,
        force=force,
    )
    if reused:
        return reused
    settings = _settings()
    duration = float(
        clamp_clip_seconds(
            inp.get("duration_seconds"),
            default=int(
                getattr(settings, "shot_duration_seconds", None)
                or getattr(settings, "fal_video_clip_seconds", None)
                or 5
            ),
        )
    )
    image_bytes, image_mime, image_url = _shot_image_for_i2v(
        job["project_id"],
        job.get("scene_id"),
        str(shot_id) if shot_id else None,
    )
    if not image_bytes and not image_url:
        raise ValueError(
            f"Missing still image for shot {shot_id} "
            f"(scene={job.get('scene_id')}); each shot needs its own image before i2v"
        )
    extra: dict[str, Any] = {"job_id": job["id"]}
    if force:
        extra["force"] = True
        if inp.get("regen_token"):
            extra["regen_token"] = inp.get("regen_token")
    res = getattr(settings, "fal_video_resolution", None)
    if res:
        extra["resolution"] = res
    # Reserve estimated cost on the job for JOB_MAX_USD accounting.
    est = float(getattr(settings, "fal_video_cost_usd", 0.05) or 0.05)
    job_store.update_job(
        get_sync_db_from_settings(),
        job["id"],
        estimated_cost_usd=est,
        status="uploading",
        clip_state="uploading",
    )
    # Wan i2v: motion/camera only — story lives in the Flux still, not the prompt.
    from app.providers.fal.prompt_fix import rewrite_motion_prompt

    override = str(inp.get("motion_prompt") or inp.get("prompt_override") or "").strip()
    mode = str(inp.get("motion_mode") or "auto").strip() or "auto"
    if override:
        video_prompt = rewrite_motion_prompt(override, mode="guided")
    else:
        camera = str(inp.get("camera") or "").strip()
        raw = str(
            inp.get("prompt")
            or _shot_visual_description(
                job["project_id"],
                scene_id=job.get("scene_id"),
                shot_id=str(shot_id) if shot_id else None,
                inp=inp,
            )
            or ""
        ).strip()
        video_prompt = rewrite_motion_prompt(raw, camera=camera, mode=mode)
    # Do not append locale/story locks to Wan — image already carries look.
    result = get_registry().generate_video(
        VideoRequest(
            project_id=job["project_id"],
            scene_id=job.get("scene_id"),
            shot_id=shot_id,
            prompt=video_prompt,
            duration_seconds=duration,
            image_url=image_url,
            image_bytes=image_bytes,
            image_mime=image_mime,
            extra=extra,
        )
    )
    return _record_and_upload(job=job, result=result, asset_type="video")


def _audio_context(job: dict[str, Any]) -> dict[str, Any]:
    from app.workers.project_context import spoken_text_for_scene

    inp = job.get("input") or {}
    project = get_project_doc(job["project_id"]) or {}
    language = str(inp.get("language") or project.get("language") or "Hindi").strip() or "Hindi"
    genre = str(inp.get("genre") or project.get("genre") or "Drama")
    scene_id = job.get("scene_id")
    # Prefer explicit TTS payload, then voiceOver/narration — never visual description.
    script = str(inp.get("text") or inp.get("script") or "").strip()
    if not script:
        script = spoken_text_for_scene(
            job["project_id"],
            scene_id,
            language=language,
        )
    if not script:
        script = scene_script_from_graph(job["project_id"], scene_id)
    scene_title = str(inp.get("scene_title") or scene_id or "Scene")
    scene_description = str(inp.get("scene_description") or "")
    duration = float(
        inp.get("duration_seconds")
        or inp.get("scene_duration_seconds")
        or 10
    )
    return {
        "language": language,
        "language_code": language_to_sarvam_code(language),
        "genre": genre,
        "script": script,
        "scene_title": scene_title,
        "scene_description": scene_description,
        "duration_seconds": duration,
    }


def produce_tts(job: dict[str, Any]) -> dict[str, Any]:
    """One dialogue line → one Sarvam clip with a fixed character voice."""
    enforce_budget(job["project_id"])
    inp = job.get("input") or {}
    force = bool(inp.get("force") or inp.get("regen_token"))
    line_id = str(inp.get("line_id") or job.get("shot_id") or "").strip() or None
    reused = _reuse_existing_asset(
        project_id=job["project_id"],
        asset_type="tts",
        scene_id=job.get("scene_id"),
        shot_id=line_id or job.get("shot_id"),
        force=force,
    )
    if reused:
        return reused
    ctx = _audio_context(job)
    # Prefer the single line text from the job — never join whole-scene script.
    text = str(inp.get("text") or "").strip() or ctx["script"]
    if not text:
        text = fallback_spoken_line(
            language=ctx["language"],
            scene_title=str(job.get("scene_id") or "Scene"),
        )
    voice = str(inp.get("voice") or "").strip() or "shubh"
    result = get_registry().generate_tts(
        TTSRequest(
            project_id=job["project_id"],
            scene_id=job.get("scene_id"),
            text=text,
            voice=voice,
            language=ctx["language"],
            language_code=ctx["language_code"],
            extra={
                "line_id": line_id,
                "line_index": inp.get("line_index"),
                "character_id": inp.get("character_id"),
                "character_name": inp.get("character_name"),
                "linked_shot_id": inp.get("linked_shot_id"),
            },
        )
    )
    linked_shot = str(inp.get("linked_shot_id") or "").strip() or None
    result.metadata = {
        **(result.metadata or {}),
        "line_id": line_id,
        "line_index": inp.get("line_index"),
        "character_id": inp.get("character_id"),
        "character_name": inp.get("character_name"),
        "voice": voice,
        "linked_shot_id": linked_shot,
    }
    # Ensure asset is keyed by line id for timeline assembly.
    if line_id and not job.get("shot_id"):
        job = {**job, "shot_id": line_id}
    out = _record_and_upload(job=job, result=result, asset_type="tts")
    # Expose probed duration for callers / timeline builders.
    if out.get("duration") is not None:
        out["line_duration_seconds"] = float(out["duration"])
    return out


def produce_sfx(job: dict[str, Any]) -> dict[str, Any]:
    enforce_budget(job["project_id"])
    inp = job.get("input") or {}
    force = bool(inp.get("force") or inp.get("regen_token"))
    reused = _reuse_existing_asset(
        project_id=job["project_id"],
        asset_type="sfx",
        scene_id=job.get("scene_id"),
        shot_id=job.get("shot_id"),
        force=force,
    )
    if reused:
        return reused
    ctx = _audio_context(job)
    prompt = build_sfx_prompt(
        genre=ctx["genre"],
        scene_title=ctx["scene_title"],
        scene_description=ctx["scene_description"],
    )
    video_bytes, video_mime, video_url = _scene_media_for_type(
        job["project_id"],
        job.get("scene_id"),
        "video",
    )
    result = get_registry().generate_sfx(
        SFXRequest(
            project_id=job["project_id"],
            scene_id=job.get("scene_id"),
            prompt=prompt,
            duration_seconds=min(30.0, max(0.5, ctx["duration_seconds"])),
            genre=ctx["genre"],
            loop=True,
            video_url=video_url,
            video_bytes=video_bytes,
            video_mime=video_mime,
        )
    )
    return _record_and_upload(job=job, result=result, asset_type="sfx")


def produce_music(job: dict[str, Any]) -> dict[str, Any]:
    """Project-level theme track (one per project). Scenes only trim later."""
    enforce_budget(job["project_id"])
    inp = job.get("input") or {}
    force = bool(inp.get("force") or inp.get("regen_token"))
    # Theme jobs are project-scoped (no scene_id).
    scene_id = job.get("scene_id") if inp.get("kind") != "theme" else None
    reused = _reuse_existing_asset(
        project_id=job["project_id"],
        asset_type="music",
        scene_id=scene_id,
        shot_id=job.get("shot_id"),
        force=force,
    )
    if reused:
        return reused
    ctx = _audio_context(job)
    # Prefer full-project duration for the theme bed.
    duration = float(inp.get("duration_seconds") or ctx["duration_seconds"] or 30)
    if inp.get("kind") == "theme" or not job.get("scene_id"):
        try:
            from app.workers.project_context import load_director_plan_for_production

            loaded = load_director_plan_for_production(job["project_id"])
            if loaded:
                _plan, scenes, _shots = loaded
                total = sum(float(s.get("duration_seconds") or 0) for s in scenes)
                if total > 0:
                    duration = total
        except Exception:
            pass
    project = get_project_doc(job["project_id"]) or {}
    title = str(
        inp.get("scene_title") or project.get("title") or "Theme"
    )
    description = str(
        inp.get("scene_description")
        or project.get("concept")
        or ctx["scene_description"]
        or ctx["genre"]
        or "cinematic score"
    )
    prompt = build_music_prompt(
        genre=ctx["genre"],
        scene_title=title,
        scene_description=description,
    )
    length_ms = int(min(600_000, max(3000, duration * 1000)))
    result = get_registry().generate_music(
        MusicRequest(
            project_id=job["project_id"],
            scene_id=None,
            prompt=prompt,
            duration_seconds=duration,
            music_length_ms=length_ms,
            genre=ctx["genre"],
            force_instrumental=True,
        )
    )
    result.metadata = {
        **(result.metadata or {}),
        "kind": "theme",
        "project_theme": True,
    }
    # Force project-level asset (no scene_id) for theme reuse across scenes.
    theme_job = {**job, "scene_id": None, "shot_id": None}
    return _record_and_upload(job=theme_job, result=result, asset_type="music")


def produce_passthrough_media(
    job: dict[str, Any],
    *,
    asset_type: AssetKind,
    filename: str,
    mime_type: str,
    data: bytes,
    cost_usd: float = 0.0,
    duration: float | None = None,
) -> dict[str, Any]:
    """Upload non-provider worker outputs (ffmpeg / assembly) via the same storage path."""
    result = ProviderResult(
        data=data,
        mime_type=mime_type,
        filename=filename,
        cost_usd=cost_usd,
        provider_name="ffmpeg",
        duration_seconds=duration,
        metadata={"pipeline": asset_type},
    )
    return _record_and_upload(job=job, result=result, asset_type=asset_type)
