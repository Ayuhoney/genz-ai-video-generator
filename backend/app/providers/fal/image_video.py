"""Fal image + video providers (queue API). Model IDs come from env only."""

from __future__ import annotations

from typing import Any

from app.providers.base import (
    ImageProvider,
    ImageRequest,
    ProviderResult,
    VideoProvider,
    VideoRequest,
)
from app.providers.fal.client import (
    FalAPIError,
    FalClient,
    extract_image_url,
    extract_video_url,
)
from app.providers.fal.clip_timing import clamp_clip_seconds, soft_visual_prompt, wan_frame_args


def _fal_settings() -> Any:
    try:
        from app.core.config import get_settings

        return get_settings()
    except Exception:
        from app.workers.settings import get_worker_settings

        return get_worker_settings()


def _make_client() -> FalClient:
    s = _fal_settings()
    key = (s.fal_key or "").strip()
    return FalClient(
        key,
        poll_interval=s.fal_poll_interval_seconds,
        poll_timeout=s.fal_poll_timeout_seconds,
        webhook_url=s.fal_webhook_url,
    )


def _estimate_image_cost(metrics: dict[str, Any], s: Any) -> float:
    flat = float(s.fal_image_cost_usd or 0.0)
    per_sec = float(getattr(s, "fal_image_cost_per_second", 0.0) or 0.0)
    inference = float(metrics.get("inference_time") or 0.0)
    if per_sec > 0 and inference > 0:
        return round(per_sec * inference, 6)
    return flat


def _estimate_video_cost(metrics: dict[str, Any], duration: float, s: Any) -> float:
    per_sec = float(s.fal_video_cost_per_second or 0.0)
    inference = float(metrics.get("inference_time") or 0.0)
    if per_sec > 0 and inference > 0:
        return round(per_sec * inference, 6)
    if per_sec > 0 and duration > 0:
        return round(per_sec * duration, 6)
    return float(s.fal_video_cost_usd or 0.0)


class FalImageProvider(ImageProvider):
    name = "fal"

    def generate(self, request: ImageRequest) -> ProviderResult:
        s = _fal_settings()
        model = (s.fal_image_model or "").strip()
        if not model:
            raise FalAPIError("FAL_IMAGE_MODEL is required when PROVIDER_IMAGE=fal")

        arguments: dict[str, Any] = {
            "prompt": soft_visual_prompt(request.prompt or "cinematic scene still"),
        }
        # Common optional knobs — only send if present in extra / request.
        if request.width:
            arguments["image_size"] = {
                "width": request.width,
                "height": request.height,
            }
        refs = [
            u.strip()
            for u in (request.reference_image_urls or [])
            if isinstance(u, str) and u.strip()
        ]
        if refs:
            arguments["prompt"] = (
                f"{arguments['prompt']}. Keep the exact same face identity "
                f"as the locked character reference photo in every shot."
            )
            model_l = model.lower()
            supports_ref = any(
                token in model_l
                for token in (
                    "image-to-image",
                    "pulid",
                    "ip-adapter",
                    "faceid",
                    "face-to",
                    "consistent-character",
                )
            )
            if supports_ref:
                arguments.setdefault("image_url", refs[0])
                if len(refs) > 1:
                    arguments.setdefault("image_urls", refs)
        arguments.update(request.extra or {})

        with _make_client() as client:
            result, metrics = client.run(model, arguments)
            url = extract_image_url(result)
            data = client.download(url)
            content_type = "image/png"
            images = result.get("images")
            if isinstance(images, list) and images and isinstance(images[0], dict):
                content_type = str(images[0].get("content_type") or content_type)

        return ProviderResult(
            data=data,
            mime_type=content_type,
            filename="image.png" if "png" in content_type else "image.jpg",
            metadata={
                "prompt": request.prompt,
                "model": model,
                "fal_metrics": metrics,
                "source_url": url,
            },
            cost_usd=_estimate_image_cost(metrics, s),
            provider_name=self.name,
            url=url,
        )


class FalVideoProvider(VideoProvider):
    """Image-to-video via fal queue. Requires image_url (or image bytes to upload)."""

    name = "fal"

    def generate(self, request: VideoRequest) -> ProviderResult:
        s = _fal_settings()
        model = (s.fal_video_model or "").strip()
        if not model:
            raise FalAPIError("FAL_VIDEO_MODEL is required when PROVIDER_VIDEO=fal")

        duration = clamp_clip_seconds(
            request.duration_seconds,
            default=int(getattr(s, "fal_video_clip_seconds", None) or 15),
        )
        image_url = (request.image_url or "").strip() or None

        with _make_client() as client:
            if not image_url and request.image_bytes:
                image_url = client.upload_bytes(
                    request.image_bytes,
                    content_type=request.image_mime or "image/png",
                    file_name="scene.png",
                )
            if not image_url:
                raise FalAPIError(
                    "FalVideoProvider requires image_url or image_bytes for image-to-video"
                )

            arguments: dict[str, Any] = {
                "prompt": soft_visual_prompt(
                    request.prompt or "subtle cinematic camera motion"
                ),
                "image_url": image_url,
            }
            # WAN models expect num_frames/fps — plain "duration" is ignored / wrong.
            model_l = model.lower()
            if "wan" in model_l:
                arguments.update(wan_frame_args(duration))
            else:
                arguments["duration"] = duration
            arguments.update(request.extra or {})

            result, metrics = client.run(model, arguments)
            url = extract_video_url(result)
            data = client.download(url)
            content_type = "video/mp4"
            video = result.get("video")
            if isinstance(video, dict) and video.get("content_type"):
                content_type = str(video["content_type"])

        return ProviderResult(
            data=data,
            mime_type=content_type,
            filename="clip.mp4",
            metadata={
                "prompt": request.prompt,
                "model": model,
                "image_url": image_url,
                "requested_duration": duration,
                "fal_args": {
                    k: arguments.get(k)
                    for k in (
                        "num_frames",
                        "frames_per_second",
                        "num_interpolated_frames",
                        "duration",
                    )
                    if k in arguments
                },
                "fal_metrics": metrics,
                "source_url": url,
            },
            cost_usd=_estimate_video_cost(metrics, float(duration), s),
            provider_name=self.name,
            duration_seconds=float(duration),
            url=url,
        )
