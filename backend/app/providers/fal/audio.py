"""fal.ai SFX + music providers (queue API). Model IDs from env only."""

from __future__ import annotations

from typing import Any

from app.providers.base import (
    MusicProvider,
    MusicRequest,
    ProviderResult,
    SFXProvider,
    SFXRequest,
)
from app.providers.fal.client import FalAPIError, FalClient


def _fal_settings() -> Any:
    try:
        from app.core.config import get_settings

        return get_settings()
    except Exception:
        from app.workers.settings import get_worker_settings

        return get_worker_settings()


def _make_client() -> FalClient:
    s = _fal_settings()
    # Keep audio polls short so hung mmaudio jobs cannot pin both Celery slots.
    video_poll = float(getattr(s, "fal_poll_timeout_seconds", 600) or 600)
    audio_poll = float(getattr(s, "fal_audio_poll_timeout_seconds", 180) or 180)
    poll_timeout = max(30.0, min(audio_poll, video_poll))
    return FalClient(
        (s.fal_key or "").strip(),
        poll_interval=s.fal_poll_interval_seconds,
        poll_timeout=poll_timeout,
        webhook_url=getattr(s, "fal_webhook_url", None),
    )


def _extract_audio_url(result: dict[str, Any]) -> str:
    for key in ("audio_file", "audio"):
        obj = result.get(key)
        if isinstance(obj, dict) and obj.get("url"):
            return str(obj["url"])
        if isinstance(obj, str) and obj.startswith("http"):
            return obj
    if isinstance(result.get("url"), str):
        return str(result["url"])
    # mmaudio returns video with embedded audio
    video = result.get("video")
    if isinstance(video, dict) and video.get("url"):
        return str(video["url"])
    raise FalAPIError(f"No audio/video URL in fal result keys={list(result.keys())}")


class FalSFXProvider(SFXProvider):
    """Sound effects via fal (e.g. fal-ai/mmaudio-v2). Prefers video_url when set."""

    name = "fal"

    def generate(self, request: SFXRequest) -> ProviderResult:
        s = _fal_settings()
        model = (request.model_id or getattr(s, "fal_sfx_model", None) or "").strip()
        if not model:
            raise FalAPIError("FAL_SFX_MODEL is required when PROVIDER_SFX=fal")

        duration = max(0.5, min(30.0, float(request.duration_seconds or 5)))
        arguments: dict[str, Any] = {
            "prompt": request.prompt or "cinematic ambient foley",
            "negative_prompt": "music, speech, voice, talking, singing, humming",
            "duration": round(duration, 1),
            "num_steps": 25,
        }
        video_url = (request.video_url or "").strip() or None
        with _make_client() as client:
            if not video_url and request.video_bytes:
                video_url = client.upload_bytes(
                    request.video_bytes,
                    content_type=request.video_mime or "video/mp4",
                    file_name="scene.mp4",
                )
            if video_url:
                arguments["video_url"] = video_url
            arguments.update(request.extra or {})
            result, metrics = client.run(model, arguments)
            url = _extract_audio_url(result)
            data = client.download(url)

        # mmaudio often returns mp4; store as-is (ffmpeg assembly handles extract)
        mime = "video/mp4" if url.endswith(".mp4") or "video" in str(result.get("video")) else "audio/wav"
        if isinstance(result.get("video"), dict):
            mime = str(result["video"].get("content_type") or mime)

        cost = float(getattr(s, "fal_sfx_cost_usd", 0.006) or 0.006)
        # Normalize to wav for assembly when model returns video container (mmaudio).
        if "video" in mime or data[:4] == b"ftyp" or (len(data) > 8 and data[4:8] == b"ftyp"):
            data = _extract_wav_bytes(data)
            mime = "audio/wav"

        return ProviderResult(
            data=data,
            mime_type=mime,
            filename="sfx.wav",
            metadata={
                "prompt": request.prompt,
                "model": model,
                "fal_metrics": metrics,
                "source_url": url,
                "video_url": video_url,
            },
            cost_usd=cost,
            provider_name=self.name,
            duration_seconds=duration,
            url=url,
        )


def _extract_wav_bytes(media: bytes) -> bytes:
    import tempfile
    from pathlib import Path

    from app.media.ffmpeg_tools import run_ffmpeg

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.mp4"
        dst = Path(tmp) / "out.wav"
        src.write_bytes(media)
        run_ffmpeg(["-i", str(src), "-vn", "-ac", "2", "-ar", "44100", str(dst)])
        return dst.read_bytes()


class FalMusicProvider(MusicProvider):
    name = "fal"

    def generate(self, request: MusicRequest) -> ProviderResult:
        s = _fal_settings()
        model = (request.model_id or getattr(s, "fal_music_model", None) or "").strip()
        if not model:
            raise FalAPIError("FAL_MUSIC_MODEL is required when PROVIDER_MUSIC=fal")

        if request.music_length_ms:
            duration = max(3.0, min(120.0, request.music_length_ms / 1000.0))
        else:
            duration = max(3.0, min(120.0, float(request.duration_seconds or 30)))

        arguments: dict[str, Any] = {
            "prompt": request.prompt or "cinematic instrumental score, no vocals",
            "duration": int(round(duration)),
        }
        arguments.update(request.extra or {})

        with _make_client() as client:
            result, metrics = client.run(model, arguments)
            url = _extract_audio_url(result)
            data = client.download(url)

        cost = float(getattr(s, "fal_music_cost_usd", 0.01) or 0.01)
        return ProviderResult(
            data=data,
            mime_type="audio/wav",
            filename="music.wav",
            metadata={
                "prompt": request.prompt,
                "model": model,
                "fal_metrics": metrics,
                "source_url": url,
            },
            cost_usd=cost,
            provider_name=self.name,
            duration_seconds=duration,
            url=url,
        )
