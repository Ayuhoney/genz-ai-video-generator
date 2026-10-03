"""Rewrite user / shot text into Wan-safe motion prompts (optional Groq)."""

from __future__ import annotations

import logging

from app.providers.fal.clip_timing import (
    DEFAULT_SAFE_MOTION_PROMPT,
    motion_only_prompt,
    ultra_safe_motion_prompt,
)

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You rewrite video motion prompts for an image-to-video model. "
    "Output ONE short English sentence (max 40 words) describing ONLY camera "
    "movement and atmosphere (pan, push-in, slow motion, dust, lighting). "
    "Do NOT name weapons, fights, blood, death, or plot events. "
    "Do NOT describe who the character is or what story happens — the photo "
    "already shows that. Reply with the prompt text only, no quotes."
)


def rewrite_motion_prompt(
    text: str | None,
    *,
    camera: str | None = None,
    mode: str = "auto",
) -> str:
    """Build a Wan-safe motion prompt.

    mode:
      - auto: extract camera cues or use default safe motion
      - ultra: always DEFAULT_SAFE_MOTION_PROMPT
      - guided: user notes → AI rewrite when Groq available, else strip+wrap
    """
    if mode == "ultra":
        return ultra_safe_motion_prompt()

    raw = " ".join(p for p in ((camera or "").strip(), (text or "").strip()) if p)
    if mode == "guided" and raw:
        ai = _try_groq_motion_rewrite(raw)
        if ai:
            return motion_only_prompt(ai)
        return motion_only_prompt(raw, camera=camera)

    return motion_only_prompt(text, camera=camera)


def _try_groq_motion_rewrite(user_text: str) -> str | None:
    """Plain-text Groq rewrite (not JSON) — best-effort, never raises."""
    try:
        import httpx

        from app.orchestration.director_ai import (
            DEFAULT_GROQ_MODEL,
            GROQ_CHAT_URL,
            groq_enabled,
        )
        from app.core.config import get_settings
    except Exception:
        return None
    if not groq_enabled():
        return None
    try:
        s = get_settings()
        key = (getattr(s, "groq_key", None) or "").strip()
        model = (getattr(s, "groq_model", None) or DEFAULT_GROQ_MODEL).strip()
        if not key:
            return None
        body = {
            "model": model,
            "temperature": 0.2,
            "max_completion_tokens": 120,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "User notes for camera/mood (may include story — strip it):\n"
                        f"{user_text[:500]}"
                    ),
                },
            ],
        }
        with httpx.Client(timeout=45.0) as client:
            r = client.post(
                GROQ_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        if r.status_code >= 400:
            return None
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            return None
        out = str((choices[0].get("message") or {}).get("content") or "").strip()
        out = out.strip("\"'` ")
        if not out or len(out) > 280:
            return None
        low = out.lower()
        banned = ("sword", "weapon", "fight", "kill", "blood", "gun", "battle")
        if any(b in low for b in banned):
            return DEFAULT_SAFE_MOTION_PROMPT
        return out
    except Exception as exc:  # noqa: BLE001
        logger.info("motion prompt AI rewrite skipped: %s", exc)
        return None
