"""Sync Mongo helpers for worker jobs (project + scene script)."""

from __future__ import annotations

from typing import Any

from bson import ObjectId

from app.storage.asset_store import get_sync_db_from_settings


def get_project_doc(project_id: str) -> dict[str, Any] | None:
    db = get_sync_db_from_settings()
    filters: list[dict[str, Any]] = [{"_id": project_id}]
    if ObjectId.is_valid(project_id):
        filters.append({"_id": ObjectId(project_id)})
    return db.projects.find_one({"$or": filters})


def join_voice_over_text(voice_over: Any) -> str:
    """Concatenate spoken lines from voiceOver / voice_over entries."""
    if not isinstance(voice_over, list):
        return ""
    parts: list[str] = []
    for item in voice_over:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
        else:
            text = str(getattr(item, "text", "") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def fallback_spoken_line(*, language: str, scene_title: str) -> str:
    """Language-aware TTS fallback when voiceOver is missing (never English visuals)."""
    title = (scene_title or "Scene").strip() or "Scene"
    key = (language or "Hindi").strip().lower()
    if key.startswith("hindi") or key in {"hi", "hi-in"}:
        return f"{title}। कहानी आगे बढ़ती है।"
    if key.startswith("tamil") or key in {"ta", "ta-in"}:
        return f"{title}. கதை தொடர்கிறது."
    if key.startswith("telugu") or key in {"te", "te-in"}:
        return f"{title}. కథ కొనసాగుతుంది."
    if key.startswith("bengali") or key.startswith("bangla") or key in {"bn", "bn-in"}:
        return f"{title}। গল্প এগোয়।"
    if key.startswith("marathi") or key in {"mr", "mr-in"}:
        return f"{title}. कथा पुढे सुरू आहे."
    if key.startswith("gujarati") or key in {"gu", "gu-in"}:
        return f"{title}. વાર્તા આગળ વધે છે."
    if key.startswith("kannada") or key in {"kn", "kn-in"}:
        return f"{title}. ಕಥೆ ಮುಂದುವರಿಯುತ್ತದೆ."
    if key.startswith("malayalam") or key in {"ml", "ml-in"}:
        return f"{title}. കഥ തുടരുന്നു."
    if key.startswith("punjabi") or key in {"pa", "pa-in"}:
        return f"{title}. ਕਹਾਣੀ ਅੱਗੇ ਵਧਦੀ ਹੈ।"
    if key.startswith("odia") or key.startswith("oriya") or key in {"od", "or", "od-in"}:
        return f"{title}. କାହାଣୀ ଆଗକୁ ବଢ଼େ।"
    return f"{title}. The story continues."


def spoken_text_from_scene(
    scene: dict[str, Any] | None,
    *,
    language: str | None = None,
) -> str:
    """Prefer voiceOver/narration; never use visual description as spoken TTS."""
    if not isinstance(scene, dict):
        return ""
    for key in ("narration", "spoken_text"):
        raw = str(scene.get(key) or "").strip()
        if raw:
            return raw
    joined = join_voice_over_text(
        scene.get("voice_over") or scene.get("voiceOver")
    )
    if joined:
        return joined
    # Explicit script field only when it looks like narration (set by dispatch).
    script = str(scene.get("script") or "").strip()
    if script:
        return script
    title = str(scene.get("title") or scene.get("id") or "Scene")
    if language:
        return fallback_spoken_line(language=language, scene_title=title)
    return ""


def spoken_text_for_scene(
    project_id: str,
    scene_id: str | None,
    *,
    scene: dict[str, Any] | None = None,
    language: str | None = None,
) -> str:
    """Resolve TTS text: scene state → saved director voiceOver → language fallback."""
    doc = get_project_doc(project_id) or {}
    lang = (language or "").strip() or str(doc.get("language") or "Hindi")

    direct = spoken_text_from_scene(scene, language=None)
    if direct:
        return direct

    if not scene_id:
        return fallback_spoken_line(language=lang, scene_title="Scene")

    director = doc.get("director_response") or doc.get("directorResponse") or {}
    if isinstance(director, dict):
        for sc in director.get("scenes") or []:
            if not isinstance(sc, dict):
                continue
            if str(sc.get("id") or "") != str(scene_id):
                continue
            text = spoken_text_from_scene(sc, language=None)
            if text:
                return text
            title = str(sc.get("title") or scene_id)
            return fallback_spoken_line(language=lang, scene_title=title)

    title = str((scene or {}).get("title") or scene_id)
    return fallback_spoken_line(language=lang, scene_title=title)


def load_director_plan_for_production(
    project_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Hydrate LangGraph scenes/shots from the confirmed AI Director response."""
    doc = get_project_doc(project_id) or {}
    language = str(doc.get("language") or "Hindi")
    director = doc.get("director_response") or doc.get("directorResponse") or {}
    if not isinstance(director, dict):
        return None
    raw_scenes = director.get("scenes") or []
    if not isinstance(raw_scenes, list) or not raw_scenes:
        return None

    from app.providers.fal.clip_timing import clamp_clip_seconds

    scenes: list[dict[str, Any]] = []
    shots: list[dict[str, Any]] = []
    for index, sc in enumerate(raw_scenes, start=1):
        if not isinstance(sc, dict):
            continue
        sid = str(sc.get("id") or f"scene-{index}")
        title = str(sc.get("title") or f"Scene {index}")
        desc = str(sc.get("description") or "")
        scene_shots = sc.get("shots") or []
        voice_over = sc.get("voiceOver") or sc.get("voice_over") or []
        if not isinstance(voice_over, list):
            voice_over = []
        vo_norm: list[dict[str, Any]] = []
        for i, vo in enumerate(voice_over, start=1):
            if not isinstance(vo, dict):
                continue
            text = str(vo.get("text") or "").strip()
            if not text:
                continue
            vo_norm.append(
                {
                    "id": str(vo.get("id") or f"{sid}-vo-{i}"),
                    "character_id": vo.get("characterId") or vo.get("character_id"),
                    "character_name": str(
                        vo.get("characterName")
                        or vo.get("character_name")
                        or "Narrator"
                    ),
                    "text": text,
                    "estimated_seconds": float(
                        vo.get("estimatedSeconds")
                        or vo.get("estimated_seconds")
                        or 3.0
                    ),
                }
            )
        narration = join_voice_over_text(vo_norm)
        if not narration:
            narration = fallback_spoken_line(language=language, scene_title=title)
        scene_dur = int(
            sc.get("durationSeconds")
            or sc.get("duration_seconds")
            or 0
        )
        if not scene_dur and isinstance(scene_shots, list):
            scene_dur = int(
                round(
                    sum(
                        float(
                            sh.get("durationSeconds")
                            or sh.get("duration_seconds")
                            or 15
                        )
                        for sh in scene_shots
                        if isinstance(sh, dict)
                    )
                )
            )
        scenes.append(
            {
                "id": sid,
                "order": int(sc.get("order") or index),
                "title": title,
                "description": desc or title,
                "duration_seconds": max(5, scene_dur or 15),
                "status": "pending",
                "narration": narration,
                "voice_over": vo_norm,
            }
        )
        if isinstance(scene_shots, list) and scene_shots:
            for s_i, sh in enumerate(scene_shots, start=1):
                if not isinstance(sh, dict):
                    continue
                shots.append(
                    {
                        "id": str(sh.get("id") or f"{sid}-shot-{s_i}"),
                        "scene_id": sid,
                        "order": int(sh.get("order") or s_i),
                        "title": str(sh.get("title") or f"{title} / Shot {s_i}"),
                        "description": str(sh.get("description") or desc or title),
                        "status": "pending",
                        "duration_seconds": float(
                            clamp_clip_seconds(
                                sh.get("durationSeconds")
                                or sh.get("duration_seconds")
                                or 15
                            )
                        ),
                    }
                )
        else:
            shots.append(
                {
                    "id": f"{sid}-shot-1",
                    "scene_id": sid,
                    "order": 1,
                    "title": f"{title} / Shot 1",
                    "description": desc or title,
                    "status": "pending",
                    "duration_seconds": float(clamp_clip_seconds(scene_dur or 15)),
                }
            )

    if not scenes:
        return None

    plan = {
        "project_id": project_id,
        "title": str(director.get("title") or doc.get("title") or project_id),
        "provider": "saved_director",
        "beats": [
            str(b)
            for b in (
                director.get("storyStructure")
                or director.get("story_structure")
                or []
            )
        ],
        "script": str(director.get("script") or ""),
        "concept": str(director.get("concept") or doc.get("concept") or ""),
        "language": language,
    }
    return plan, scenes, shots


def locked_character_refs(project_id: str) -> list[dict[str, str]]:
    """Return face-locked characters with reference image URLs from director plan."""
    doc = get_project_doc(project_id) or {}
    director = doc.get("director_response") or doc.get("directorResponse") or {}
    if not isinstance(director, dict):
        return []
    chars = director.get("characters") or []
    out: list[dict[str, str]] = []
    for char in chars:
        if not isinstance(char, dict):
            continue
        locked = bool(char.get("face_locked") or char.get("faceLocked"))
        url = (
            char.get("reference_image_url")
            or char.get("referenceImageUrl")
            or ""
        ).strip()
        if locked and url:
            out.append(
                {
                    "id": str(char.get("id") or ""),
                    "name": str(char.get("name") or "Character"),
                    "description": str(char.get("description") or ""),
                    "url": url,
                }
            )
    return out


def language_to_iso639_1(language: str | None) -> str | None:
    """Map project language labels to ISO 639-1 / Sarvam-friendly codes."""
    if not language:
        return None
    key = language.strip().lower()
    mapping = {
        "english": "en",
        "english (india)": "en",
        "en": "en",
        "hindi": "hi",
        "hi": "hi",
        "bengali": "bn",
        "bangla": "bn",
        "gujarati": "gu",
        "kannada": "kn",
        "malayalam": "ml",
        "marathi": "mr",
        "odia": "or",
        "oriya": "or",
        "punjabi": "pa",
        "tamil": "ta",
        "telugu": "te",
    }
    return mapping.get(key)


def scene_script_from_graph(project_id: str, scene_id: str | None) -> str:
    """Read spoken narration for TTS from graph state (not visual description)."""
    if not scene_id:
        return ""
    try:
        from app.orchestration.checkpointing import thread_config
        from app.orchestration.graph import compile_graph

        graph = compile_graph()
        snapshot = graph.get_state(thread_config(project_id))
        values = snapshot.values or {}
        for scene in values.get("scenes") or []:
            if scene.get("id") == scene_id:
                text = spoken_text_from_scene(scene, language=None)
                if text:
                    return text
                break
    except Exception:
        pass
    return spoken_text_for_scene(project_id, scene_id)


def build_sfx_prompt(*, genre: str, scene_title: str, scene_description: str) -> str:
    mood = scene_description or scene_title or "cinematic scene"
    return (
        f"Ambient sound design for a {genre} film scene: {mood}. "
        "Subtle foley and atmosphere, no speech, suitable for video background."
    )


def build_music_prompt(*, genre: str, scene_title: str, scene_description: str) -> str:
    mood = scene_description or scene_title or "emotional arc"
    return (
        f"Instrumental background score for a {genre} scene titled '{scene_title}'. "
        f"Mood: {mood}. Seamless loop-friendly bed, no vocals, cinematic production."
    )
