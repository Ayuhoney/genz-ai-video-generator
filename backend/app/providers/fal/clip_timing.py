"""Shared clip-length rules for fal WAN image-to-video."""

from __future__ import annotations

import re

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

# Wan i2v: never send story/weapons — only camera + atmosphere (fal policy-safe).
DEFAULT_SAFE_MOTION_PROMPT = (
    "Cinematic slow motion, camera pans smoothly around the character, "
    "dust particles floating in the air, cinematic studio lighting, "
    "professional movie sequence, no deformation."
)

_MOTION_MARKER = "Cinematic slow motion,"


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


# Appended after story text for *image* (Flux) prompts only.
_SOFT_VISUAL_SUFFIX = (
    "Cinematic film still, dramatic lighting, premium Hollywood color grade, "
    "tasteful PG-13 drama."
)


def soft_visual_prompt(text: str) -> str:
    """Keep scene text; strip blocked words only (no meaning rewrites).

    Idempotent: already-wrapped prompts are returned unchanged.
    """
    raw = (text or "").strip() or "cinematic film moment"
    if "Cinematic film still, dramatic lighting, premium Hollywood color grade" in raw:
        return raw
    base = _soften_blocked_terms(raw) or "cinematic film moment"
    return f"{base}. {_SOFT_VISUAL_SUFFIX}"


_CAMERA_CUE_RE = re.compile(
    r"("
    r"(?:slow\s+)?(?:push[- ]?in|pull[- ]?out|pan|tilt|dolly|orbit|track(?:ing)?|"
    r"crane|handheld|steadicam|zoom|parallax|rack\s+focus)|"
    r"(?:wide|medium|close[- ]?up|establishing)\s+shot|"
    r"(?:slow\s+motion|cinematic\s+lighting|studio\s+lighting|"
    r"dust\s+particles|volumetric\s+light|god\s+rays|lens\s+flare|"
    r"shallow\s+depth|bokeh|golden\s+hour|soft\s+light)"
    r")",
    re.IGNORECASE,
)

# Extra strip for Wan prompts (story/weapons must not reach the video model).
_WAN_STORY_BLOCKED: tuple[str, ...] = (
    "sword",
    "talwar",
    "arrow",
    "bow",
    "spear",
    "blade",
    "weapon",
    "gun",
    "rifle",
    "pistol",
    "knife",
    "dagger",
    "fight",
    "battle",
    "war",
    "kill",
    "murder",
    "attack",
    "violence",
    "violent",
    "blood",
    "bloody",
    "gore",
    "wound",
    "explode",
    "explosion",
    "decapitat",
    "dismember",
    "mutilat",
    "suicide",
    "rape",
    "porn",
    "nsfw",
    "nude",
    "naked",
    "sex",
)


def _extract_camera_cues(text: str) -> str:
    """Pull only camera/atmosphere phrases; drop story/weapons."""
    cleaned = _soften_blocked_terms(text or "", extra=_WAN_STORY_BLOCKED)
    if not cleaned:
        return ""
    hits = [m.group(0).strip() for m in _CAMERA_CUE_RE.finditer(cleaned)]
    # Dedupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for h in hits:
        key = h.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
    if uniq:
        return ", ".join(uniq)
    # If the whole string looks like a short camera note (no long story), keep it.
    words = cleaned.split()
    if len(words) <= 12 and not any(
        w in cleaned.lower()
        for w in ("rescue", "kidnap", "criminal", "enemy", "warrior", "ramayan", "rama")
    ):
        return cleaned
    return ""


def motion_only_prompt(
    text: str | None = None,
    *,
    camera: str | None = None,
) -> str:
    """Wan i2v prompt: camera + atmosphere only — never full story/weapons.

    Idempotent when already a safe motion prompt.
    """
    raw = (text or "").strip()
    if (
        _MOTION_MARKER in raw
        or raw.startswith("Cinematic camera:")
        or DEFAULT_SAFE_MOTION_PROMPT.rstrip(".") in raw.rstrip(".")
    ):
        # Already motion-shaped — return canonical safe prompt (idempotent).
        if DEFAULT_SAFE_MOTION_PROMPT.rstrip(".") in raw.rstrip("."):
            # Keep any leading camera cues before the safe block.
            idx = raw.rstrip(".").find(DEFAULT_SAFE_MOTION_PROMPT.rstrip("."))
            prefix = raw[:idx].strip(" .,")
            if prefix:
                scrubbed = _soften_blocked_terms(prefix, extra=_WAN_STORY_BLOCKED)
                if scrubbed:
                    return f"{scrubbed}. {DEFAULT_SAFE_MOTION_PROMPT}"
            return DEFAULT_SAFE_MOTION_PROMPT
        scrubbed = _soften_blocked_terms(raw, extra=_WAN_STORY_BLOCKED)
        return scrubbed or DEFAULT_SAFE_MOTION_PROMPT

    cues: list[str] = []
    cam = _extract_camera_cues(camera or "")
    if cam:
        cues.append(cam)
    from_text = _extract_camera_cues(raw)
    if from_text and from_text.lower() not in {c.lower() for c in cues}:
        cues.append(from_text)

    if not cues:
        return DEFAULT_SAFE_MOTION_PROMPT
    return f"{', '.join(cues)}. {DEFAULT_SAFE_MOTION_PROMPT}"


def ultra_safe_motion_prompt(text: str | None = None) -> str:
    """Fallback after content reject — fixed motion-only prompt (ignores story)."""
    _ = text  # intentionally unused; image carries the scene
    return DEFAULT_SAFE_MOTION_PROMPT


# Strip-only blocklist for image prompts (no synonym rewrites).
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


def _soften_blocked_terms(
    text: str,
    *,
    extra: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Remove blocked words only — never rewrite story meaning."""
    out = text or ""
    words = list(_BLOCKED_WORDS)
    if extra:
        words.extend(extra)
    for word in sorted(set(words), key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(word)}\w*\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+,", ",", out)
    out = out.strip(" ,.-")
    return out or ""


# Back-compat alias used by older imports/tests.
def _scrub_risky_words(text: str) -> str:
    return _soften_blocked_terms(text) or "cinematic film moment"
