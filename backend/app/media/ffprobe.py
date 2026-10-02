"""Audio/video duration via ffprobe (optional — returns None if unavailable)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def probe_duration_seconds(data: bytes, *, suffix: str = ".bin") -> float | None:
    """Return media duration in seconds using ffprobe, or None on failure."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="probe-"))
    path = tmp / f"media{suffix}"
    try:
        path.write_bytes(data)
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            return None
        line = (proc.stdout or "").strip()
        if not line:
            return None
        return float(line)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    finally:
        try:
            if path.is_file():
                path.unlink()
            tmp.rmdir()
        except OSError:
            pass


def probe_file(path: Path) -> dict[str, float | bool]:
    """Return duration and stream flags for a media file on disk."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found")
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()}")
    import json

    data = json.loads(proc.stdout or "{}")
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    streams = data.get("streams") or []
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return {
        "duration": duration,
        "has_video": has_video,
        "has_audio": has_audio,
    }


def suffix_for_mime(mime_type: str) -> str:
    mapping = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "video/mp4": ".mp4",
        "image/png": ".png",
    }
    return mapping.get(mime_type.split(";")[0].strip().lower(), ".bin")
