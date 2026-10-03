"""Shared clip-length rules for fal WAN image-to-video."""

from __future__ import annotations

# WAN v2.2 a14b hard ceiling for a single fal clip (~15s).
FAL_WAN_MAX_CLIP_SECONDS = 15
# Planned shots may be shorter (remainder beats); fal still clamps at submit.
FAL_WAN_MIN_CLIP_SECONDS = 1
# Default max length per shot when splitting a scene (SHOT_DURATION_SECONDS).
DEFAULT_CLIP_SECONDS = 5

_SHOT_BEAT_LABELS = (
    "wide establishing shot",
    "medium shot",
    "close-up",
)


def clip_seconds_from_settings(settings: object | None = None) -> int:
    """Max planned shot length from SHOT_DURATION_SECONDS (fallback FAL_VIDEO_CLIP_SECONDS)."""
    raw = DEFAULT_CLIP_SECONDS
    if settings is not None:
        raw = int(
            getattr(settings, "shot_duration_seconds", None)
            or getattr(settings, "fal_video_clip_seconds", None)
            or DEFAULT_CLIP_SECONDS
        )
    return max(1, min(FAL_WAN_MAX_CLIP_SECONDS, raw))


def clamp_clip_seconds(seconds: float | int | None, *, default: int = DEFAULT_CLIP_SECONDS) -> int:
    try:
        value = float(seconds) if seconds is not None else float(default)
    except (TypeError, ValueError):
        value = float(default)
    return max(FAL_WAN_MIN_CLIP_SECONDS, min(FAL_WAN_MAX_CLIP_SECONDS, int(round(value))))


def wan_frame_args(duration_seconds: float | int) -> dict[str, int | bool]:
    """Map target seconds → WAN queue arguments (single clip, max ~15s)."""
    # WAN full model is unstable below ~5s; keep a practical floor for frames.
    seconds = max(5, clamp_clip_seconds(duration_seconds))
    # Prefer max frames; pick FPS so duration ≈ seconds without interpolation.
    num_frames = 161
    fps = max(4, min(60, int(round(num_frames / seconds))))
    # Recompute actual length; nudge fps if needed.
    actual = num_frames / fps
    if actual > seconds + 0.6 and fps < 60:
        fps = min(60, fps + 1)
    elif actual < seconds - 0.6 and fps > 4:
        fps = max(4, fps - 1)
    return {
        "num_frames": num_frames,
        "frames_per_second": fps,
        "num_interpolated_frames": 0,
        "adjust_fps_for_interpolation": False,
    }


def plan_shot_durations(
    total_seconds: int,
    *,
    max_clip: int = DEFAULT_CLIP_SECONDS,
    min_clip: int = 1,
) -> list[int]:
    """Split scene runtime into shots of at most max_clip that sum exactly.

    Examples (max_clip=5): 15→[5,5,5], 12→[5,5,2], 7→[5,2], 5→[5], 3→[3].
    """
    max_clip = max(1, int(max_clip))
    min_clip = max(1, min(int(min_clip), max_clip))
    total = max(min_clip, int(total_seconds))
    if total <= max_clip:
        return [total]
    n_full = total // max_clip
    rem = total % max_clip
    durations = [max_clip] * n_full
    if rem > 0:
        durations.append(rem)
    return durations


def plan_shot_count(total_seconds: int, clip: int = DEFAULT_CLIP_SECONDS) -> int:
    clip = max(1, int(clip))
    return max(1, len(plan_shot_durations(total_seconds, max_clip=clip)))


def shot_beat_description(scene_text: str, index: int, total: int) -> str:
    """Distinct coverage text per shot (wide / medium / close-up / beat N)."""
    base = (scene_text or "").strip() or "cinematic scene"
    if total <= 1:
        return f"wide establishing shot: {base}"
    if index <= len(_SHOT_BEAT_LABELS):
        label = _SHOT_BEAT_LABELS[index - 1]
    else:
        label = f"alternate angle beat {index}"
    return f"{label}: {base}"


# Appended after story text. Must NOT contain fal-flagged tokens (gore/blood/injury
# even as "no gore" still trip wan turbo content checker).
_SOFT_VISUAL_SUFFIX = (
    "Cinematic film still, dramatic lighting, premium Hollywood color grade, "
    "tasteful PG-13 drama."
)
_MOTION_SAFE_SUFFIX = "Tasteful cinematic motion, premium color grade."


def soft_visual_prompt(text: str) -> str:
    """Keep scene text; strip blocked words only (no meaning rewrites).

    Idempotent: already-wrapped prompts are returned unchanged.
    """
    raw = (text or "").strip() or "cinematic film moment"
    if "Cinematic film still, dramatic lighting, premium Hollywood color grade" in raw:
        return raw
    base = _soften_blocked_terms(raw)
    return f"{base}. {_SOFT_VISUAL_SUFFIX}"


def motion_only_prompt(text: str | None = None) -> str:
    """Keep full scene/shot text; only strip blocked words. Add light camera cue.

    Idempotent: already-wrapped prompts are returned unchanged.
    """
    raw = (text or "").strip()
    marker = "Cinematic camera motion,"
    if marker in raw or raw.startswith("Cinematic camera:") or raw.startswith(
        "Dynamic cinematic camera:"
    ):
        return raw
    scene = _soften_blocked_terms(raw)
    if not scene:
        return ultra_safe_motion_prompt()
    return f"{scene}. {marker} natural movement, dramatic lighting. {_MOTION_SAFE_SUFFIX}"


def ultra_safe_motion_prompt(text: str | None = None) -> str:
    """Fallback after content reject — still keeps scene text when available."""
    scene = _soften_blocked_terms((text or "").strip())
    base = (
        "Cinematic camera: slow push-in, subtle parallax, natural ambient movement, "
        f"warm dramatic lighting. {_MOTION_SAFE_SUFFIX}"
    )
    if scene:
        return f"{scene}. {base}"
    return base


# Strip-only blocklist (no synonym rewrites that change story meaning).
_BLOCKED_WORDS: tuple[str, ...] = (
    "decapitation",
    "dismemberment",
    "mutilation",
    "suicide",
    "rape",
    "porn",
    "nsfw",
    "gore",
    "blood",
    "bloody",
    "nude",
    "naked",
    "sex",
)


def _soften_blocked_terms(text: str) -> str:
    """Remove blocked words only — never rewrite story meaning."""
    import re

    out = text or ""
    for word in sorted(_BLOCKED_WORDS, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(word)}\w*\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+,", ",", out)
    out = out.strip(" ,.-")
    return out or "cinematic film moment"


# Back-compat alias used by older imports/tests.
def _scrub_risky_words(text: str) -> str:
    return _soften_blocked_terms(text)
