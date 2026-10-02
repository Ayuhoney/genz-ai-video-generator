"""Generate via provider registry, upload to storage, record asset metadata."""

from __future__ import annotations

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
from app.providers.fal.clip_timing import (
    clamp_clip_seconds,
    motion_only_prompt,
    soft_visual_prompt,
)
from app.workers import job_store
from app.workers.project_context import (
    build_music_prompt,
    build_sfx_prompt,
    fallback_spoken_line,
    get_project_doc,
    locked_character_refs,
    scene_script_from_graph,
)

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
    """If this scene/shot already has a saved asset on disk, skip fal and reuse it."""
    if force:
        return None
    db = get_sync_db_from_settings()
    assets = list_assets_for_project(db, project_id)
    matches = [
        a
        for a in assets
        if a.get("type") == asset_type
        and (scene_id is None or a.get("scene_id") == scene_id)
        and (
            shot_id is None
            or not shot_id
            or a.get("shot_id") == shot_id
            or (asset_type == "image" and not a.get("shot_id"))
        )
    ]
    if not matches:
        return None
    asset = matches[-1]
    key = str(asset.get("r2_key") or "")
    if not key:
        return None
    storage = get_storage()
    try:
        if not storage.exists(key):
            return None
    except Exception:
        return None
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


def _scene_image_for_i2v(
    project_id: str,
    scene_id: str | None,
) -> tuple[bytes | None, str | None, str | None]:
    """Return (bytes, mime, public_or_presigned_url) for the scene still."""
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


def produce_image(job: dict[str, Any]) -> dict[str, Any]:
    enforce_budget(job["project_id"])
    inp = job.get("input") or {}
    force = bool(inp.get("force") or inp.get("regen_token"))
    reused = _reuse_existing_asset(
        project_id=job["project_id"],
        asset_type="image",
        scene_id=job.get("scene_id"),
        shot_id=job.get("shot_id"),
        force=force,
    )
    if reused:
        return reused
    scene_id = job.get("scene_id")
    visual = str(
        inp.get("prompt")
        or _scene_visual_description(job["project_id"], scene_id)
        or inp.get("kind")
        or "cinematic film still, dramatic lighting, 16:9, no text"
    )
    prompt = soft_visual_prompt(visual)
    refs = locked_character_refs(job["project_id"])
    if refs:
        looks = "; ".join(
            f"{r['name']}: {r['description']}" for r in refs if r.get("description")
        )
        prompt = (
            f"{prompt}. FACE LOCK — same identity every shot for: {looks}. "
            "Do not change face, age, or hairstyle."
        )
    result = get_registry().generate_image(
        ImageRequest(
            project_id=job["project_id"],
            scene_id=scene_id,
            prompt=prompt,
            width=1280,
            height=720,
            reference_image_urls=[r["url"] for r in refs if r.get("url")],
        )
    )
    return _record_and_upload(job=job, result=result, asset_type="image")


def produce_video(job: dict[str, Any]) -> dict[str, Any]:
    """Image-to-video: scene still → clip; duration clamped to fal WAN max (~15s)."""
    enforce_budget(job["project_id"])
    inp = job.get("input") or {}
    force = bool(inp.get("force") or inp.get("regen_token"))
    reused = _reuse_existing_asset(
        project_id=job["project_id"],
        asset_type="video",
        scene_id=job.get("scene_id"),
        shot_id=job.get("shot_id"),
        force=force,
    )
    if reused:
        return reused
    settings = _settings()
    duration = float(
        clamp_clip_seconds(
            inp.get("duration_seconds"),
            default=int(getattr(settings, "fal_video_clip_seconds", None) or 15),
        )
    )
    image_bytes, image_mime, image_url = _scene_image_for_i2v(
        job["project_id"],
        job.get("scene_id"),
    )
    extra: dict[str, Any] = {}
    res = getattr(settings, "fal_video_resolution", None)
    if res:
        extra["resolution"] = res
    result = get_registry().generate_video(
        VideoRequest(
            project_id=job["project_id"],
            scene_id=job.get("scene_id"),
            shot_id=job.get("shot_id"),
            # i2v: image already carries the scene — prompt is camera motion only.
            prompt=motion_only_prompt(
                str(inp.get("prompt") or inp.get("camera") or "")
            ),
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
    enforce_budget(job["project_id"])
    ctx = _audio_context(job)
    text = ctx["script"] or fallback_spoken_line(
        language=ctx["language"],
        scene_title=str(job.get("scene_id") or "Scene"),
    )
    result = get_registry().generate_tts(
        TTSRequest(
            project_id=job["project_id"],
            scene_id=job.get("scene_id"),
            text=text,
            language=ctx["language"],
            language_code=ctx["language_code"],
        )
    )
    return _record_and_upload(job=job, result=result, asset_type="tts")


def produce_sfx(job: dict[str, Any]) -> dict[str, Any]:
    enforce_budget(job["project_id"])
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
    enforce_budget(job["project_id"])
    ctx = _audio_context(job)
    prompt = build_music_prompt(
        genre=ctx["genre"],
        scene_title=ctx["scene_title"],
        scene_description=ctx["scene_description"],
    )
    length_ms = int(min(600_000, max(3000, ctx["duration_seconds"] * 1000)))
    result = get_registry().generate_music(
        MusicRequest(
            project_id=job["project_id"],
            scene_id=job.get("scene_id"),
            prompt=prompt,
            duration_seconds=ctx["duration_seconds"],
            music_length_ms=length_ms,
            genre=ctx["genre"],
            force_instrumental=True,
        )
    )
    return _record_and_upload(job=job, result=result, asset_type="music")


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
