"""Gemini-powered director for LangGraph (falls back to mock when disabled)."""

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


def gemini_enabled() -> bool:
    s = _settings()
    return bool(
        director_provider() == "gemini"
        and (getattr(s, "gemini_key", None) or "").strip()
        and (getattr(s, "gemini_model", None) or "").strip()
    )


def _parse_json(text: str) -> dict[str, Any]:
    t = re.sub(r"```(?:json)?", "", text or "")
    if "{" not in t:
        raise ValueError("Gemini response missing JSON")
    chunk = t[t.index("{") :]
    try:
        return json.loads(chunk[: chunk.rindex("}") + 1])
    except Exception:
        if repair_json:
            return json.loads(repair_json(chunk))
        raise


def _gemini_chat(prompt: str, system: str) -> str:
    s = _settings()
    key = (s.gemini_key or "").strip()
    model = (s.gemini_model or "").strip()
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 8000,
            "responseMimeType": "application/json",
        },
        "systemInstruction": {"parts": [{"text": system}]},
    }
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=body,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"gemini {r.status_code}: {r.text[:300]}")
        cands = r.json().get("candidates") or []
        parts = (cands[0].get("content") or {}).get("parts", []) if cands else []
        text = "".join(p.get("text", "") for p in parts)
        if not text.strip():
            raise RuntimeError("gemini empty response")
        return text


DIRECTOR_SYS = """You are a film director for a short AI video.
Return ONLY valid JSON:
{"title": str,
 "scenes": [{"id": "scene-1", "order": 1, "title": str,
   "description": "What we SEE + spoken narration draft (max 25 words spoken)",
   "duration_seconds": number between 6 and 20,
   "motion": "camera + subject movement for ~5s clips",
   "sfx": "comma-separated ambient sounds, no speech",
   "shots": [{"order": 1, "title": str, "description": str, "duration_seconds": number}]}]}
Rules: 2-6 scenes; each scene 1-2 shots; total spoken text short; cinematic; no gore/blood/nudity;
never use double quotes inside string values; use target language for spoken lines inside description."""


def plan_with_gemini(
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
        f"Project: {project_id}\nGenre: {genre}\nLanguage for spoken lines: {language}\n"
        f"Target total duration about {duration_seconds}s\nScenes: {n}\n"
        f"Story/idea: {story or 'A short cinematic story'}"
    )
    raw = _gemini_chat(prompt, DIRECTOR_SYS)
    data = _parse_json(raw)
    title = str(data.get("title") or f"Film {project_id[:8]}")
    plan = {
        "project_id": project_id,
        "title": title,
        "provider": "gemini",
        "beats": [str(s.get("title") or "") for s in (data.get("scenes") or [])],
        "raw": {"genre": genre, "language": language},
    }
    scenes: list[SceneState] = []
    shots: list[ShotState] = []
    for index, sc in enumerate((data.get("scenes") or [])[:n], start=1):
        sid = str(sc.get("id") or f"scene-{index}")
        dur = float(sc.get("duration_seconds") or max(8, duration_seconds // n))
        dur = max(6.0, min(24.0, dur))
        desc = str(sc.get("description") or sc.get("action") or "").strip()
        motion = str(sc.get("motion") or "").strip()
        sfx = str(sc.get("sfx") or "").strip()
        if motion:
            desc = f"{desc} Motion: {motion}".strip()
        if sfx:
            desc = f"{desc} SFX: {sfx}".strip()
        scenes.append(
            {
                "id": sid,
                "order": int(sc.get("order") or index),
                "title": str(sc.get("title") or f"Scene {index}"),
                "description": desc or f"Scene {index}",
                "duration_seconds": dur,
                "status": "pending",
            }
        )
        shot_specs = sc.get("shots") or []
        if not shot_specs:
            half = max(3.0, round(dur / 2, 2))
            shot_specs = [
                {
                    "order": 1,
                    "title": f"{sc.get('title') or sid} A",
                    "description": motion or desc,
                    "duration_seconds": half,
                },
                {
                    "order": 2,
                    "title": f"{sc.get('title') or sid} B",
                    "description": desc,
                    "duration_seconds": max(3.0, round(dur - half, 2)),
                },
            ]
        for s_index, sh in enumerate(shot_specs[:3], start=1):
            shots.append(
                {
                    "id": f"{sid}-shot-{s_index}",
                    "scene_id": sid,
                    "order": int(sh.get("order") or s_index),
                    "title": str(sh.get("title") or f"{sid} / Shot {s_index}"),
                    "description": str(sh.get("description") or desc),
                    "status": "pending",
                    "duration_seconds": float(sh.get("duration_seconds") or max(3.0, dur / 2)),
                }
            )
    if not scenes:
        raise RuntimeError("Gemini returned no scenes")
    return plan, scenes, shots


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
