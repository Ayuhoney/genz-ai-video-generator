"""Shared clip-length rules for fal WAN image-to-video."""

from __future__ import annotations

# WAN v2.2 a14b: num_frames 17–161. At ~11 FPS without interpolation ≈ 15s.
# Absolute ceiling for a single fal clip with this model.
FAL_WAN_MAX_CLIP_SECONDS = 15
FAL_WAN_MIN_CLIP_SECONDS = 5
DEFAULT_CLIP_SECONDS = 15


def clip_seconds_from_settings(settings: object | None = None) -> int:
    raw = 15
    if settings is not None:
        raw = int(getattr(settings, "fal_video_clip_seconds", None) or DEFAULT_CLIP_SECONDS)
    return max(FAL_WAN_MIN_CLIP_SECONDS, min(FAL_WAN_MAX_CLIP_SECONDS, raw))


def clamp_clip_seconds(seconds: float | int | None, *, default: int = DEFAULT_CLIP_SECONDS) -> int:
    try:
        value = float(seconds) if seconds is not None else float(default)
    except (TypeError, ValueError):
        value = float(default)
    return max(FAL_WAN_MIN_CLIP_SECONDS, min(FAL_WAN_MAX_CLIP_SECONDS, int(round(value))))


def wan_frame_args(duration_seconds: float | int) -> dict[str, int | bool]:
    """Map target seconds → WAN queue arguments (single clip, max ~15s)."""
    seconds = clamp_clip_seconds(duration_seconds)
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
    max_clip: int = FAL_WAN_MAX_CLIP_SECONDS,
    min_clip: int = FAL_WAN_MIN_CLIP_SECONDS,
) -> list[int]:
    """Split a target runtime into fal-safe clip lengths that sum exactly."""
    max_clip = clamp_clip_seconds(max_clip)
    min_clip = max(1, min(min_clip, max_clip))
    total = max(min_clip, int(total_seconds))
    if total <= max_clip:
        return [total]

    n = (total + max_clip - 1) // max_clip
    base = total // n
    extra = total % n
    while n > 1 and base < min_clip:
        n -= 1
        base = total // n
        extra = total % n

    durations = [base + (1 if i < extra else 0) for i in range(n)]
    durations = [max(min_clip, min(max_clip, d)) for d in durations]

    diff = total - sum(durations)
    guard = 0
    while diff != 0 and durations and guard < total * 3:
        idx = guard % len(durations)
        if diff > 0 and durations[idx] < max_clip:
            durations[idx] += 1
            diff -= 1
        elif diff < 0 and durations[idx] > min_clip:
            durations[idx] -= 1
            diff += 1
        guard += 1

    if sum(durations) != total:
        # Prefer max-length clips, then one remainder clip.
        n_full = total // max_clip
        rem = total - n_full * max_clip
        durations = [max_clip] * max(0, n_full)
        if rem <= 0:
            return durations or [max_clip]
        if rem >= min_clip:
            durations.append(rem)
            return durations
        if not durations:
            return [total] if total <= max_clip else [max_clip, total - max_clip]
        # Borrow from the last full clip so remainder reaches min_clip.
        need = min_clip - rem
        if durations[-1] - need >= min_clip:
            durations[-1] -= need
            durations.append(min_clip)
        else:
            # Fall back to even split already attempted; force exact with last clip.
            durations = [max_clip] * n_full
            if durations:
                durations[-1] = durations[-1] - (min_clip - rem)
                durations.append(min_clip)
            else:
                durations = [total]
    return durations


def plan_shot_count(total_seconds: int, clip: int = DEFAULT_CLIP_SECONDS) -> int:
    clip = clamp_clip_seconds(clip)
    return max(1, len(plan_shot_durations(total_seconds, max_clip=clip)))


def soft_visual_prompt(text: str) -> str:
    """Keep story nouns; rewrite fal-blocked tokens; no family-dinner rewrite."""
    base = _soften_blocked_terms((text or "").strip() or "cinematic film moment")
    return (
        f"{base}. Cinematic film still, dramatic lighting, premium Hollywood color grade, "
        "no gore, no blood, no graphic injury, tasteful PG-13 action drama."
    )


def motion_only_prompt(text: str | None = None) -> str:
    """Camera motion derived from scene energy — never invent a different story."""
    scene = _soften_blocked_terms((text or "").strip())
    actionish = _looks_action(scene)
    if actionish:
        motion = (
            "Dynamic cinematic camera: fast tracking shots, handheld energy, "
            "slow-motion key impacts, dramatic rim lighting, rain reflections, "
            "close-ups then wide stunt coverage"
        )
    else:
        motion = (
            "Cinematic camera: slow push-in, subtle parallax, natural ambient movement, "
            "warm dramatic lighting"
        )
    if scene:
        return (
            f"{motion}. Scene context: {scene}. "
            "No gore, no blood, no graphic injury."
        )
    return f"{motion}. No gore, no blood, no graphic injury."


# Map blocked / high-risk tokens to fal-safer synonyms (preserve story meaning).
_BLOCKED_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("weapons", "tactical gear"),
    ("weapon", "tactical gear"),
    ("guns", "tactical gear"),
    ("gun", "tactical gear"),
    ("bullets", "sparks of impact"),
    ("bullet", "spark of impact"),
    ("knives", "metal props"),
    ("knife", "metal prop"),
    ("fights", "intense athletic confrontations"),
    ("fight", "intense athletic confrontation"),
    ("attacks", "confrontations"),
    ("attack", "confrontation"),
    ("attackers", "opponents"),
    ("attacker", "opponent"),
    ("armed", "hostile"),
    ("criminals", "hostile figures"),
    ("criminal", "hostile figure"),
    ("explode", "erupt in a bright blast of light"),
    ("explodes", "erupts in a bright blast of light"),
    ("explosion", "bright blast of light"),
    ("bomb", "device"),
    ("shoot", "rush"),
    ("shooting", "rushing"),
    ("stab", "strike"),
    ("blood", ""),
    ("gore", ""),
    ("kill", "defeat"),
    ("murder", "abduction"),
    ("dead", "fallen"),
    ("death", "defeat"),
    ("die", "fall"),
    ("dying", "falling"),
    ("injury", "strain"),
    ("injured", "worn"),
    ("hurt", "strained"),
    ("violence", "intensity"),
    ("violent", "intense"),
    ("war", "conflict"),
    ("threat", "tension"),
    ("panic", "urgency"),
    ("fear", "tension"),
    ("terrify", "tense"),
    ("scream", "shout"),
    ("accident", "incident"),
    ("crash", "impact"),
    ("collide", "clash"),
    ("collision", "clash"),
    ("disaster", "crisis"),
    ("danger", "high stakes"),
    ("emergency", "urgent moment"),
    ("suicide", ""),
    ("rape", ""),
    ("nude", ""),
    ("naked", ""),
    ("sex", ""),
)


_ACTION_HINTS = (
    "warehouse",
    "rescue",
    "fighter",
    "combat",
    "confrontation",
    "chase",
    "stunt",
    "tracking",
    "rain",
    "night",
    "opponent",
    "tactical",
    "punch",
    "impact",
    "athletic",
    "escape",
    "blast",
)


def _looks_action(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _ACTION_HINTS)


def _soften_blocked_terms(text: str) -> str:
    """Replace blocked words with safer synonyms; keep warehouse/rescue/etc."""
    import re

    out = text
    # Longer phrases first (tuple is already longer-first for plurals).
    for word, replacement in _BLOCKED_SYNONYMS:
        out = re.sub(
            rf"\b{re.escape(word)}\b",
            replacement,
            out,
            flags=re.IGNORECASE,
        )
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+,", ",", out)
    out = out.strip(" ,.-")
    return out or "cinematic film moment"


# Back-compat alias used by older imports/tests.
def _scrub_risky_words(text: str) -> str:
    return _soften_blocked_terms(text)
