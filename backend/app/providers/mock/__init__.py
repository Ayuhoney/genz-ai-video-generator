"""Mock generation providers (default). No real AI APIs."""

from __future__ import annotations

import struct
import zlib

from app.providers.base import (
    ImageProvider,
    ImageRequest,
    MusicProvider,
    MusicRequest,
    ProviderResult,
    SFXProvider,
    SFXRequest,
    TTSProvider,
    TTSRequest,
    VideoProvider,
    VideoRequest,
)


def mock_allowed() -> bool:
    from app.workers.settings import get_worker_settings

    return bool(get_worker_settings().allow_mock)


def _require_mock_allowed() -> None:
    if not mock_allowed():
        raise RuntimeError(
            "Mock providers are disabled (ALLOW_MOCK=false). "
            "Configure real providers or set ALLOW_MOCK=true for local/dev only."
        )

# Minimal valid 1x1 PNG
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _wav_bytes(duration_seconds: float = 0.1, sample_rate: int = 8000) -> bytes:
    n_samples = max(1, int(sample_rate * duration_seconds))
    data_size = n_samples * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )
    return header + (b"\x00\x00" * n_samples)


try:
    from app.media.lavfi_fixtures import ffmpeg_available, tiny_video_mp4_bytes

    _lavfi_video_bytes = tiny_video_mp4_bytes if ffmpeg_available() else None
except Exception:
    _lavfi_video_bytes = None


def _fake_mp4_bytes(label: str) -> bytes:
    # Not a real MP4 — opaque mock payload with a recognizable prefix.
    payload = f"mock-mp4:{label}".encode("utf-8")
    # Keep a stable non-empty blob; zlib so it isn't trivially empty.
    return b"ftypMOCK" + zlib.compress(payload)


class MockImageProvider(ImageProvider):
    name = "mock"

    def generate(self, request: ImageRequest) -> ProviderResult:
        _require_mock_allowed()
        return ProviderResult(
            data=_PNG_1X1,
            mime_type="image/png",
            filename="image.png",
            metadata={
                "prompt": request.prompt,
                "width": request.width,
                "height": request.height,
            },
            cost_usd=0.001,
            provider_name=self.name,
        )


class MockVideoProvider(VideoProvider):
    name = "mock"

    def generate(self, request: VideoRequest) -> ProviderResult:
        _require_mock_allowed()
        duration = max(0.2, min(float(request.duration_seconds or 0.5), 1.0))
        data: bytes
        if _lavfi_video_bytes is not None:
            data = _lavfi_video_bytes(duration=duration)
        else:
            data = _fake_mp4_bytes(request.shot_id or request.scene_id or "clip")
        return ProviderResult(
            data=data,
            mime_type="video/mp4",
            filename="clip.mp4",
            metadata={"prompt": request.prompt, "lavfi": _lavfi_video_bytes is not None},
            cost_usd=0.01,
            provider_name=self.name,
            duration_seconds=duration,
        )


class MockTTSProvider(TTSProvider):
    name = "mock"

    def generate(self, request: TTSRequest) -> ProviderResult:
        _require_mock_allowed()
        return ProviderResult(
            data=_wav_bytes(0.15),
            mime_type="audio/wav",
            filename="voice.wav",
            metadata={"text": request.text, "voice": request.voice},
            cost_usd=0.002,
            provider_name=self.name,
            duration_seconds=0.15,
        )


class MockSFXProvider(SFXProvider):
    name = "mock"

    def generate(self, request: SFXRequest) -> ProviderResult:
        _require_mock_allowed()
        return ProviderResult(
            data=_wav_bytes(request.duration_seconds),
            mime_type="audio/wav",
            filename="sfx.wav",
            metadata={"prompt": request.prompt},
            cost_usd=0.0015,
            provider_name=self.name,
            duration_seconds=request.duration_seconds,
        )


class MockMusicProvider(MusicProvider):
    name = "mock"

    def generate(self, request: MusicRequest) -> ProviderResult:
        _require_mock_allowed()
        return ProviderResult(
            data=_wav_bytes(min(request.duration_seconds, 1.0)),
            mime_type="audio/wav",
            filename="music.wav",
            metadata={"prompt": request.prompt},
            cost_usd=0.005,
            provider_name=self.name,
            duration_seconds=request.duration_seconds,
        )
