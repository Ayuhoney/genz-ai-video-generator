"""Per-model fal image-to-video argument adapters (docs-verified)."""

from __future__ import annotations

from typing import Any, Protocol

from app.providers.fal.clip_timing import clamp_clip_seconds, wan_frame_args

WAN_TURBO = "fal-ai/wan/v2.2-a14b/image-to-video/turbo"
WAN_FULL = "fal-ai/wan/v2.2-a14b/image-to-video"

# Default ordered chain — override via FAL_VIDEO_MODEL_CHAIN.
DEFAULT_VIDEO_MODEL_CHAIN = f"{WAN_TURBO},{WAN_FULL}"


class VideoModelAdapter(Protocol):
    model_id: str

    def build_arguments(
        self,
        *,
        prompt: str,
        image_url: str,
        duration_seconds: float,
        resolution: str,
        disable_safety_checker: bool,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def estimate_cost_usd(self, *, duration_seconds: float, settings: Any) -> float: ...


class WanTurboAdapter:
    """fal-ai/wan/v2.2-a14b/image-to-video/turbo — no num_frames; uses resolution."""

    model_id = WAN_TURBO

    def build_arguments(
        self,
        *,
        prompt: str,
        image_url: str,
        duration_seconds: float,
        resolution: str,
        disable_safety_checker: bool,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = duration_seconds  # turbo length is model-controlled
        args: dict[str, Any] = {
            "prompt": prompt,
            "image_url": image_url,
            "resolution": resolution or "480p",
            "aspect_ratio": "auto",
            "enable_prompt_expansion": False,
            "acceleration": "regular",
            "enable_output_safety_checker": False,
            "enable_safety_checker": not disable_safety_checker,
        }
        if extra:
            cleaned = {k: v for k, v in extra.items() if k not in {"prompt", "image_url"}}
            args.update(cleaned)
        return args

    def estimate_cost_usd(self, *, duration_seconds: float, settings: Any) -> float:
        per = float(getattr(settings, "fal_video_cost_per_second", 0) or 0)
        flat = float(getattr(settings, "fal_video_cost_usd", 0.05) or 0.05)
        if per > 0:
            return round(per * max(1.0, float(duration_seconds)), 6)
        return flat


class WanFullAdapter:
    """fal-ai/wan/v2.2-a14b/image-to-video — num_frames / fps / guidance from docs."""

    model_id = WAN_FULL

    def build_arguments(
        self,
        *,
        prompt: str,
        image_url: str,
        duration_seconds: float,
        resolution: str,
        disable_safety_checker: bool,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        frames = wan_frame_args(clamp_clip_seconds(duration_seconds))
        args: dict[str, Any] = {
            "prompt": prompt,
            "image_url": image_url,
            **frames,
            "resolution": resolution or "480p",
            "aspect_ratio": "auto",
            "num_inference_steps": 27,
            "enable_prompt_expansion": False,
            "acceleration": "regular",
            "guidance_scale": 3.5,
            "guidance_scale_2": 3.5,
            "shift": 5,
            "enable_output_safety_checker": False,
            "enable_safety_checker": not disable_safety_checker,
        }
        if extra:
            cleaned = {k: v for k, v in extra.items() if k not in {"prompt", "image_url"}}
            args.update(cleaned)
        return args

    def estimate_cost_usd(self, *, duration_seconds: float, settings: Any) -> float:
        # Full WAN is typically costlier; allow override, else 1.5× turbo flat.
        per = float(getattr(settings, "fal_video_cost_per_second", 0) or 0)
        flat = float(getattr(settings, "fal_video_cost_usd", 0.05) or 0.05)
        if per > 0:
            return round(per * 1.5 * max(1.0, float(duration_seconds)), 6)
        return round(flat * 1.5, 6)


_ADAPTERS: dict[str, VideoModelAdapter] = {
    WAN_TURBO: WanTurboAdapter(),
    WAN_FULL: WanFullAdapter(),
}


def get_adapter(model_id: str) -> VideoModelAdapter | None:
    mid = (model_id or "").strip()
    if mid in _ADAPTERS:
        return _ADAPTERS[mid]
    # Fuzzy: turbo before full.
    low = mid.lower()
    if "wan" in low and "turbo" in low:
        return _ADAPTERS[WAN_TURBO]
    if "wan" in low and "image-to-video" in low:
        return _ADAPTERS[WAN_FULL]
    return None


def parse_model_chain(raw: str | None, *, primary: str | None = None) -> list[str]:
    """Ordered unique model IDs from env; only adapters we know are kept."""
    parts: list[str] = []
    if raw and raw.strip():
        parts.extend(p.strip() for p in raw.split(",") if p.strip())
    elif primary and primary.strip():
        parts.append(primary.strip())
        if primary.strip() != WAN_FULL:
            parts.append(WAN_FULL)
    else:
        parts = [p.strip() for p in DEFAULT_VIDEO_MODEL_CHAIN.split(",")]

    out: list[str] = []
    for mid in parts:
        if get_adapter(mid) is None:
            continue
        if mid not in out:
            out.append(mid)
    return out or [WAN_TURBO]
