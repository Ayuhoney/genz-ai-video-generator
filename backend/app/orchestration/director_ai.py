"""Groq-powered director for LangGraph (falls back to mock when disabled)."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.orchestration.state import SceneState, ShotState

try:
    from json_repair import repair_json
except ImportError:
    repair_json = None  # type: ignore[misc, assignment]

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


def _settings() -> Any:
    try:
        from app.core.config import get_settings

        return get_settings()
    except Exception:
        from app.workers.settings import get_worker_settings

        return get_worker_settings()


def director_provider() -> str:
    s = _settings()
    return (getattr(s, "director_provider", None) or "mock").strip().lower()


def groq_enabled() -> bool:
    s = _settings()
    return bool(
        director_provider() == "groq"
        and (getattr(s, "groq_key", None) or "").strip()
        and (getattr(s, "groq_model", None) or DEFAULT_GROQ_MODEL).strip()
    )


# Back-compat alias used by older imports / tests
def gemini_enabled() -> bool:
    return groq_enabled()


def _parse_json(text: str) -> dict[str, Any]:
    t = re.sub(r"```(?:json)?", "", text or "")
    if "{" not in t:
        raise ValueError("Director response missing JSON")
    chunk = t[t.index("{") :]
    try:
        return json.loads(chunk[: chunk.rindex("}") + 1])
    except Exception:
        if repair_json:
            return json.loads(repair_json(chunk))
        raise


def _retry_after_seconds(response: httpx.Response, body_text: str) -> float:
    header = (response.headers.get("retry-after") or "").strip()
    if header:
        try:
            return max(1.0, float(header))
        except ValueError:
            pass
    match = re.search(r"try again in ([0-9.]+)s", body_text, flags=re.I)
    if match:
        try:
            return max(1.0, float(match.group(1)) + 0.5)
        except ValueError:
            pass
    return 8.0


def _groq_chat(prompt: str, system: str, *, temperature: float = 0.2) -> str:
    import time

    s = _settings()
    key = (getattr(s, "groq_key", None) or "").strip()
    model = (getattr(s, "groq_model", None) or DEFAULT_GROQ_MODEL).strip()
    if not key:
        raise RuntimeError("GROQ_KEY required for director")
    body: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_completion_tokens": 8000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    last_err = "groq unknown error"
    with httpx.Client(timeout=120.0) as client:
        for attempt in range(5):
            r = client.post(
                GROQ_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if r.status_code == 429:
                last_err = f"groq 429: {r.text[:300]}"
                time.sleep(_retry_after_seconds(r, r.text))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"groq {r.status_code}: {r.text[:300]}")
            data = r.json()
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError("groq empty choices")
            text = str((choices[0].get("message") or {}).get("content") or "")
            if not text.strip():
                raise RuntimeError("groq empty response")
            return text
    raise RuntimeError(last_err)

# Public chat entry used by director_service
def director_chat(prompt: str, system: str, *, temperature: float = 0.2) -> str:
    return _groq_chat(prompt, system, temperature=temperature)


# Legacy name kept so older imports keep working during migration
def _gemini_chat(prompt: str, system: str) -> str:
    return director_chat(prompt, system)


DIRECTOR_SYS = """You are a film director for a short AI video.
Return ONLY valid JSON:
{"title": str,
 "scenes": [{"id": "scene-1", "order": 1, "title": str,
   "description": "What we SEE in English (visual only)",
   "narration": "spoken lines ONLY in the target language native script",
   "duration_seconds": number (sum of shot lengths),
   "motion": "camera + subject movement",
   "sfx": "comma-separated ambient sounds, no speech",
   "shots": [{"order": 1, "title": str, "description": str, "duration_seconds": 5}]}]}
Rules: each shot duration_seconds MUST be 1-5 (prefer 5); split longer scenes into multiple shots (15s→3x5);
each shot description MUST be a distinct angle/beat (wide / medium / close-up), never identical text;
enough shots to cover total duration;
cinematic PG-13 Hollywood style allowed (intense confrontations, stunts, rain, dramatic lighting);
no gore/blood/nudity; avoid naming guns/weapons (use tactical gear / opponents);
never use double quotes inside string values;
CRITICAL: narration MUST be in the selected target language native script (Hindi→Devanagari);
do NOT put English spoken lines in narration unless language is English;
follow the user's story idea exactly — keep warehouses, rescues, chases, and characters from the idea."""


def plan_with_groq(
    *,
    project_id: str,
    story: str,
    language: str,
    genre: str,
    duration_seconds: int,
    scene_count: int = 3,
) -> tuple[dict[str, Any], list[SceneState], list[ShotState]]:
    n = max(2, min(6, scene_count))
    prompt = (
        f"Project: {project_id}\nGenre: {genre}\n"
        f"TARGET LANGUAGE FOR narration: {language}\n"
        f"Write narration in {language} native script. No English dialogue unless language is English.\n"
        f"Target total duration about {duration_seconds}s\nScenes: {n}\n"
        f"Story/idea (follow exactly): {story or 'A short cinematic story'}"
    )
    raw = director_chat(prompt, DIRECTOR_SYS)
    data = _parse_json(raw)
    title = str(data.get("title") or f"Film {project_id[:8]}")
    plan = {
        "project_id": project_id,
        "title": title,
        "provider": "groq",
        "beats": [str(s.get("title") or "") for s in (data.get("scenes") or [])],
        "raw": {"genre": genre, "language": language},
        "language": language,
    }
    scenes: list[SceneState] = []
    shots: list[ShotState] = []
    from app.workers.project_context import fallback_spoken_line

    for index, sc in enumerate((data.get("scenes") or [])[:n], start=1):
        sid = str(sc.get("id") or f"scene-{index}")
        dur = float(sc.get("duration_seconds") or max(8, duration_seconds // n))
        dur = max(6.0, min(24.0, dur))
        desc = str(sc.get("description") or sc.get("action") or "").strip()
        narration = str(sc.get("narration") or "").strip()
        motion = str(sc.get("motion") or "").strip()
        sfx = str(sc.get("sfx") or "").strip()
        if motion:
            desc = f"{desc} Motion: {motion}".strip()
        if sfx:
            desc = f"{desc} SFX: {sfx}".strip()
        scene_title = str(sc.get("title") or f"Scene {index}")
        if not narration:
            narration = fallback_spoken_line(language=language, scene_title=scene_title)
        scenes.append(
            {
                "id": sid,
                "order": int(sc.get("order") or index),
                "title": scene_title,
                "description": desc or f"Scene {index}",
                "duration_seconds": dur,
                "status": "pending",
                "narration": narration,
                "voice_over": [
                    {
                        "id": f"{sid}-vo-1",
                        "character_name": "Narrator",
                        "text": narration,
                        "estimated_seconds": min(8.0, dur * 0.4),
                    }
                ],
            }
        )
        from app.providers.fal.clip_timing import (
            clip_seconds_from_settings,
            plan_shot_durations,
            shot_beat_description,
        )

        try:
            from app.core.config import get_settings

            max_shot = clip_seconds_from_settings(get_settings())
        except Exception:
            max_shot = 5
        durs = plan_shot_durations(int(round(dur)), max_clip=max_shot)
        shot_specs = sc.get("shots") or []
        for s_index, shot_dur in enumerate(durs, start=1):
            sh = shot_specs[s_index - 1] if s_index - 1 < len(shot_specs) else {}
            if not isinstance(sh, dict):
                sh = {}
            raw_desc = str(sh.get("description") or "").strip()
            if not raw_desc or (
                s_index > 1
                and raw_desc
                == str((shot_specs[0] or {}).get("description") or "").strip()
            ):
                raw_desc = shot_beat_description(desc or motion, s_index, len(durs))
            shots.append(
                {
                    "id": f"{sid}-shot-{s_index}",
                    "scene_id": sid,
                    "order": int(sh.get("order") or s_index),
                    "title": str(sh.get("title") or f"{sid} / Shot {s_index}"),
                    "description": raw_desc,
                    "status": "pending",
                    "duration_seconds": float(shot_dur),
                }
            )
    if not scenes:
        raise RuntimeError("Groq returned no scenes")
    return plan, scenes, shots


# Back-compat alias
def plan_with_gemini(
    *,
    project_id: str,
    story: str,
    language: str,
    genre: str,
    duration_seconds: int,
    scene_count: int = 3,
) -> tuple[dict[str, Any], list[SceneState], list[ShotState]]:
    return plan_with_groq(
        project_id=project_id,
        story=story,
        language=language,
        genre=genre,
        duration_seconds=duration_seconds,
        scene_count=scene_count,
    )


def load_project_story(project_id: str) -> dict[str, Any]:
    try:
        from app.workers.project_context import get_project_doc

        doc = get_project_doc(project_id) or {}
        return {
            "story": str(doc.get("concept") or doc.get("idea") or doc.get("title") or ""),
            "language": str(doc.get("language") or "Hindi"),
            "genre": str(doc.get("genre") or "Drama"),
            "duration_seconds": int(doc.get("duration_seconds") or 60),
            "title": str(doc.get("title") or ""),
        }
    except Exception:
        return {
            "story": "",
            "language": "Hindi",
            "genre": "Drama",
            "duration_seconds": 60,
            "title": "",
        }
