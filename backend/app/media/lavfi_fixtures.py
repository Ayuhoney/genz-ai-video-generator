"""Tiny media clips generated via ffmpeg lavfi (tests + mock providers)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from app.media.ffmpeg_tools import require_ffmpeg_tools, run_ffmpeg


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def write_tiny_video_mp4(
    path: Path,
    *,
    duration: float = 0.4,
    width: int = 320,
    height: int = 180,
    fps: int = 24,
) -> None:
    """H.264 + AAC MP4 with test pattern video and sine audio."""
    require_ffmpeg_tools()
    dur = max(0.1, min(float(duration), 2.0))
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={width}x{height}:rate={fps}:duration={dur}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={dur}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            str(path),
        ],
        timeout=120,
    )


def tiny_video_mp4_bytes(*, duration: float = 0.4) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "clip.mp4"
        write_tiny_video_mp4(path, duration=duration)
        return path.read_bytes()


def write_tiny_audio_wav(path: Path, *, duration: float = 0.3) -> None:
    require_ffmpeg_tools()
    dur = max(0.05, min(float(duration), 2.0))
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=880:duration={dur}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        timeout=60,
    )


def tiny_audio_wav_bytes(*, duration: float = 0.3) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "tone.wav"
        write_tiny_audio_wav(path, duration=duration)
        return path.read_bytes()
