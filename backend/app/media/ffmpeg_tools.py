"""ffmpeg/ffprobe availability checks."""

from __future__ import annotations

import shutil
import subprocess


class FFmpegNotFoundError(RuntimeError):
    pass


def require_ffmpeg_tools() -> None:
    """Raise if ffmpeg or ffprobe is missing (call at worker startup)."""
    missing = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        raise FFmpegNotFoundError(
            f"Required tools not found on PATH: {', '.join(missing)}. "
            "Install ffmpeg (includes ffprobe)."
        )


def run_ffmpeg(args: list[str], *, timeout: int = 600) -> None:
    require_ffmpeg_tools()
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
