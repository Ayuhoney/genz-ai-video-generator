"""FFmpeg scene composition and final assembly."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.media.ffmpeg_tools import run_ffmpeg
from app.media.ffprobe import probe_file


@dataclass(slots=True)
class SceneInputs:
    shot_clips: list[Path]
    narration: Path | None = None
    sfx: Path | None = None
    music: Path | None = None
    target_duration: float | None = None


class FFmpegValidationError(RuntimeError):
    pass


def _audio_chain(stream_ref: str, target_seconds: float, *, loop: bool, out: str) -> str:
    t = max(0.1, float(target_seconds))
    if loop:
        return (
            f"{stream_ref}aloop=loop=-1:size=2e+09,atrim=0:{t},"
            f"asetpts=PTS-STARTPTS{out}"
        )
    return (
        f"{stream_ref}atrim=0:{t},asetpts=PTS-STARTPTS,"
        f"apad=whole_dur={t}{out}"
    )


def render_scene_video(
    *,
    work_dir: Path,
    inputs: SceneInputs,
    output_path: Path,
    width: int = 1280,
    height: int = 720,
    fps: int = 24,
    fade_seconds: float = 0.25,
) -> float:
    """Combine shot clips + narration + SFX + music into one scene MP4."""
    work_dir.mkdir(parents=True, exist_ok=True)
    if not inputs.shot_clips:
        raise ValueError("Scene render requires at least one shot clip")

    normalized: list[Path] = []
    for index, clip in enumerate(inputs.shot_clips):
        norm = work_dir / f"shot_norm_{index}.mp4"
        run_ffmpeg(
            [
                "-i",
                str(clip),
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-an",
                str(norm),
            ]
        )
        normalized.append(norm)

    concat_list = work_dir / "shots.txt"
    concat_list.write_text(
        "".join(f"file '{p.resolve()}'\n" for p in normalized),
        encoding="utf-8",
    )
    video_only = work_dir / "video_only.mp4"
    run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(video_only),
        ]
    )

    video_info = probe_file(video_only)
    video_duration = float(inputs.target_duration or video_info["duration"] or 1.0)

    audio_specs: list[tuple[Path, bool, str]] = []
    for path, loop, tag in (
        (inputs.narration, False, "narr"),
        (inputs.sfx, True, "sfx"),
        (inputs.music, True, "mus"),
    ):
        if path is not None:
            audio_specs.append((path, loop, tag))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not audio_specs:
        run_ffmpeg(
            [
                "-i",
                str(video_only),
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r=44100:cl=stereo:d={video_duration}",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
    else:
        filter_parts: list[str] = []
        mix_labels: list[str] = []
        cmd = ["-i", str(video_only)]
        for audio_index, (path, loop, tag) in enumerate(audio_specs, start=1):
            cmd.extend(["-i", str(path)])
            out = f"[a{tag}]"
            filter_parts.append(
                _audio_chain(f"[{audio_index}:a]", video_duration, loop=loop, out=out)
            )
            mix_labels.append(out)
        mix_in = "".join(mix_labels)
        filter_complex = (
            ";".join(filter_parts)
            + f";{mix_in}amix=inputs={len(mix_labels)}:duration=longest,"
            f"afade=t=in:st=0:d={fade_seconds},"
            f"afade=t=out:st={max(0, video_duration - fade_seconds)}:d={fade_seconds},"
            "dynaudnorm=f=75:g=15[aout]"
        )
        cmd.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        run_ffmpeg(cmd)

    info = probe_file(output_path)
    if not info["has_video"]:
        raise FFmpegValidationError("Scene output missing video stream")
    return float(info["duration"])


def assemble_final_video(
    *,
    scene_videos: list[Path],
    output_path: Path,
) -> float:
    """Concatenate scene videos in order (re-encode for consistent codecs)."""
    if not scene_videos:
        raise ValueError("Final assembly requires at least one scene video")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = output_path.parent / "scenes_concat.txt"
    list_file.write_text(
        "".join(f"file '{p.resolve()}'\n" for p in scene_videos),
        encoding="utf-8",
    )
    run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    info = probe_file(output_path)
    return float(info["duration"])


def validate_final_output(
    path: Path,
    *,
    expected_duration: float | None = None,
    tolerance_seconds: float = 2.0,
) -> dict[str, float | bool]:
    """Validate playable final MP4 with video+audio streams."""
    if not path.is_file() or path.stat().st_size < 128:
        raise FFmpegValidationError("Final output file missing or too small")
    info = probe_file(path)
    if not info["has_video"] or not info["has_audio"]:
        raise FFmpegValidationError("Final output must contain video and audio streams")
    duration = float(info["duration"])
    if duration <= 0:
        raise FFmpegValidationError("Final output duration invalid")
    if expected_duration is not None:
        delta = abs(duration - expected_duration)
        if delta > tolerance_seconds:
            raise FFmpegValidationError(
                f"Final duration {duration:.2f}s outside tolerance of "
                f"{expected_duration:.2f}s (+/- {tolerance_seconds}s)"
            )
    # Quick decode sanity check (first second).
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-read_intervals", "0%+1", "-show_frames", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise FFmpegValidationError(f"Final output not playable: {proc.stderr.strip()}")
    return info
