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
    """Sanitize + append safety-friendly cinematic constraints."""
    base = _scrub_risky_words((text or "").strip() or "cinematic film moment")
    return (
        f"{base}. Family-friendly cinematic still, soft film lighting, "
        "peaceful everyday life, no gore, no blood, no weapons, no graphic violence, "
        "no injury, tasteful drama, PG rating."
    )


def motion_only_prompt(text: str | None = None) -> str:
    """i2v prompts must describe CAMERA MOTION only — narrative words trip fal checkers."""
    _ = _scrub_risky_words(text or "")
    return (
        "Gentle cinematic camera motion: slow push-in, subtle parallax, "
        "natural ambient movement, warm soft lighting, calm family-friendly mood, "
        "no violence, no injury, no weapons, peaceful everyday scene."
    )


_RISKY = (
    "accident",
    "crash",
    "collide",
    "collision",
    "emergency",
    "brake",
    "blood",
    "gore",
    "kill",
    "murder",
    "weapon",
    "gun",
    "knife",
    "fight",
    "attack",
    "hurt",
    "injury",
    "injured",
    "dead",
    "death",
    "die",
    "dying",
    "scream",
    "danger",
    "disaster",
    "explode",
    "bomb",
    "suicide",
    "rape",
    "nude",
    "naked",
    "sex",
    "violence",
    "violent",
    "war",
    "shoot",
    "stab",
    "threat",
    "panic",
    "fear",
    "terrify",
)


def _scrub_risky_words(text: str) -> str:
    import re

    out = text
    for word in _RISKY:
        out = re.sub(rf"\b{re.escape(word)}\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,.-")
    return out or "peaceful cinematic moment"
