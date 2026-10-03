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
from app.providers.fal.adapters import parse_model_chain
from app.providers.fal.client import (
    FalAPIError,
    FalClient,
    extract_image_url,
)
from app.providers.fal.clip_timing import (
    clamp_clip_seconds,
    soft_visual_prompt,
)
from app.providers.fal.errors import FalClipError
from app.providers.fal.generate_clip import ClipGenerateRequest, generate_clip
from app.workers import job_store
from app.storage.asset_store import get_sync_db_from_settings


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


def _disable_safety_checker(s: Any) -> bool:
    return bool(getattr(s, "fal_disable_safety_checker", False))


class FalImageProvider(ImageProvider):
    name = "fal"

    def generate(self, request: ImageRequest) -> ProviderResult:
        s = _fal_settings()
        model = (s.fal_image_model or "").strip()
        if not model:
            raise FalAPIError("FAL_IMAGE_MODEL is required when PROVIDER_IMAGE=fal")

        scene_text = (request.prompt or "").strip() or "cinematic scene still"
        image_prompt_final = soft_visual_prompt(scene_text)
        arguments: dict[str, Any] = {
            "prompt": image_prompt_final,
        }
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
        extra = dict(request.extra or {})
        if refs:
            image_prompt_final = (
                f"{arguments['prompt']}. Keep the exact same face identity "
                f"as the locked character reference photo in every shot."
            )
            arguments["prompt"] = image_prompt_final
            i2i_model = (
                str(extra.pop("i2i_model", None) or "").strip()
                or str(getattr(s, "fal_image_i2i_model", "") or "").strip()
            )
            model_l = (i2i_model or model).lower()
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
            if supports_ref and i2i_model:
                model = i2i_model
            if supports_ref or "image-to-image" in model.lower():
                arguments.setdefault("image_url", refs[0])
                if len(refs) > 1:
                    arguments.setdefault("image_urls", refs)
                strength = extra.pop("strength", None)
                if strength is None:
                    strength = getattr(s, "fal_image_i2i_strength", 0.65)
                try:
                    arguments.setdefault("strength", float(strength))
                except (TypeError, ValueError):
                    arguments.setdefault("strength", 0.65)
        arguments.update(extra)

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
                "scene_text": scene_text,
                "image_prompt_final": image_prompt_final,
                "video_prompt_final": None,
                "model": model,
                "fal_metrics": metrics,
                "source_url": url,
                "reference_image_url": refs[0] if refs else None,
            },
            cost_usd=_estimate_image_cost(metrics, s),
            provider_name=self.name,
            url=url,
        )


class FalVideoProvider(VideoProvider):
    """Image-to-video via fal queue + generate_clip (model chain + budgets)."""

    name = "fal"

    def generate(self, request: VideoRequest) -> ProviderResult:
        s = _fal_settings()
        primary = (s.fal_video_model or "").strip()
        if not primary:
            raise FalAPIError("FAL_VIDEO_MODEL is required when PROVIDER_VIDEO=fal")
        chain = parse_model_chain(
            getattr(s, "fal_video_model_chain", None),
            primary=primary,
        )

        duration = clamp_clip_seconds(
            request.duration_seconds,
            default=int(
                getattr(s, "shot_duration_seconds", None)
                or getattr(s, "fal_video_clip_seconds", None)
                or 5
            ),
        )
        resolution = str(
            (request.extra or {}).get("resolution")
            or getattr(s, "fal_video_resolution", None)
            or "480p"
        )
        clip_id = None
        job_id = (request.extra or {}).get("job_id")
        force = bool(
            (request.extra or {}).get("force")
            or (request.extra or {}).get("regen_token")
        )
        if request.project_id and (request.scene_id or request.shot_id):
            # Include job_id so explicit user Retry (new job) is not blocked by a
            # prior successful fal:clip:idemp key for the same shot.
            base = (
                f"{request.project_id}:{request.scene_id or '_'}:{request.shot_id or '_'}"
            )
            clip_id = f"{base}:{job_id}" if job_id else base
            if force:
                from app.providers.fal.clip_controls import idempotency_clear

                idempotency_clear(base)
                if job_id:
                    # Also clear any stale claim for this new job id.
                    idempotency_clear(str(clip_id))

        extra = dict(request.extra or {})
        extra.pop("prompt", None)
        extra.pop("resolution", None)
        extra.pop("job_id", None)
        extra.pop("force", None)
        extra.pop("regen_token", None)

        def on_state(state: str) -> None:
            if not job_id:
                return
            try:
                db = get_sync_db_from_settings()
                job_store.update_job(db, str(job_id), status=state, clip_state=state)
            except Exception:
                pass

        with _make_client() as client:
            try:
                clip = generate_clip(
                    client,
                    ClipGenerateRequest(
                        model_id=chain[0],
                        prompt=request.prompt or "",
                        image_bytes=request.image_bytes,
                        image_mime=request.image_mime,
                        image_url=request.image_url,
                        duration_seconds=float(duration),
                        resolution=resolution,
                        disable_safety_checker=_disable_safety_checker(s),
                        clip_id=str(clip_id) if clip_id else None,
                        project_id=request.project_id,
                        model_chain=chain,
                        job_id=str(job_id) if job_id else None,
                        extra=extra,
                        on_state=on_state,
                    ),
                )
            except FalClipError:
                raise
            if str(clip.video_url).startswith("dry-run://"):
                # Tiny valid-enough placeholder; zero fal spend.
                data = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
                content_type = "video/mp4"
            else:
                data = client.download(clip.video_url)
                content_type = "video/mp4"
                video = clip.result.get("video")
                if isinstance(video, dict) and video.get("content_type"):
                    content_type = str(video["content_type"])

        cost = _estimate_video_cost(clip.metrics, float(duration), s)
        if clip.estimated_cost_usd and cost <= 0:
            cost = clip.estimated_cost_usd

        scene_text = (request.prompt or "").strip()
        return ProviderResult(
            data=data,
            mime_type=content_type,
            filename="clip.mp4",
            metadata={
                "prompt": clip.prompt_used,
                "scene_text": scene_text,
                "image_prompt_final": None,
                "video_prompt_final": clip.prompt_used,
                "model": clip.model_id,
                "model_chain": clip.model_chain_used,
                "image_url": clip.image_url,
                "requested_duration": duration,
                "requested_duration_seconds": duration,
                "planned_duration_seconds": duration,
                "fal_request_id": clip.request_id,
                "fal_attempts": clip.attempts,
                "fal_prompt_sanitize_level": clip.prompt_sanitize_level,
                "estimated_cost_usd": clip.estimated_cost_usd,
                "fal_args": {"resolution": resolution},
                "fal_metrics": clip.metrics,
                "source_url": clip.video_url,
            },
            cost_usd=cost,
            provider_name=self.name,
            duration_seconds=float(duration),
            url=clip.video_url,
        )
