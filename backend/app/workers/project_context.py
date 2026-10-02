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
    """Read scene narration text from LangGraph checkpoint state if available."""
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
                title = str(scene.get("title") or "").strip()
                desc = str(scene.get("description") or "").strip()
                if title and desc:
                    return f"{title}. {desc}"
                return desc or title
    except Exception:
        pass
    return ""


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
