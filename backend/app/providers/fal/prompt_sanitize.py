"""Local prompt pre-sanitizer before any fal call (configurable blocked words)."""

from __future__ import annotations

import os
import re

# Default blocklist — can be extended via FAL_PROMPT_BLOCKED_WORDS=comma,separated
_DEFAULT_BLOCKED = (
    "gore",
    "blood",
    "bloody",
    "nsfw",
    "nude",
    "naked",
    "sex",
    "porn",
    "suicide",
    "rape",
    "decapitat",
    "dismember",
    "mutilat",
)


def _blocked_words() -> list[str]:
    extra = (os.environ.get("FAL_PROMPT_BLOCKED_WORDS") or "").strip()
    words = list(_DEFAULT_BLOCKED)
    if extra:
        words.extend(w.strip().lower() for w in extra.split(",") if w.strip())
    # Longer first for phrase-ish tokens
    return sorted(set(words), key=len, reverse=True)


def pre_sanitize_prompt(text: str | None) -> str:
    """Strip/replace locally blocked tokens before fal sees the prompt."""
    out = (text or "").strip()
    if not out:
        return "Cinematic camera: slow push-in, warm dramatic lighting."
    for word in _blocked_words():
        if not word:
            continue
        out = re.sub(rf"\b{re.escape(word)}\w*\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+,", ",", out)
    out = out.strip(" ,.-")
    return out or "Cinematic camera: slow push-in, warm dramatic lighting."
