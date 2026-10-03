"""FFmpeg scene composition and final assembly."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.media.ffmpeg_tools import run_ffmpeg
from app.media.ffprobe import probe_file

logger = logging.getLogger(__name__)

# Flag shot when |actual - planned| exceeds this (seconds). No freeze-pad.
DURATION_MISMATCH_SECONDS = 1.0


@dataclass(slots=True)
class SceneInputs:
    shot_clips: list[Path]
    narration: Path | None = None
    sfx: Path | None = None
    music: Path | None = None
    # Planned scene length (seconds). Used for mismatch checks only — output
    # follows real clip lengths (no freeze-pad to force the plan).
    target_duration: float | None = None
    # Optional per-shot plan lengths (same order as shot_clips).
    shot_target_durations: list[float] | None = None


@dataclass(slots=True)
class NormalizeResult:
    duration: float
    duration_mismatch: bool = False
    planned_seconds: float = 0.0
    actual_seconds: float = 0.0


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


def _normalize_clip(
    *,
    src: Path,
    dest: Path,
    planned_seconds: float,
    width: int,
    height: int,
    fps: int,
) -> NormalizeResult:
    """Scale/fps normalize. Trim if longer than plan; never freeze-pad shorter clips."""
    planned = max(0.0, float(planned_seconds or 0.0))
    info = probe_file(src)
    actual = float(info.get("duration") or 0.0)
    if actual <= 0:
        raise FFmpegValidationError(f"Shot clip has no duration: {src}")

    mismatch = bool(planned > 0 and abs(actual - planned) > DURATION_MISMATCH_SECONDS)
    if mismatch:
        logger.warning(
            "shot duration mismatch planned=%.3fs actual=%.3fs path=%s (no freeze-pad)",
            planned,
            actual,
            src,
        )

    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p"
    )
    # Trim only when clip overshoots the plan; otherwise keep native length.
    out_dur = actual
    if planned > 0 and actual > planned + 0.08:
        out_dur = planned
        vf = f"{scale},trim=duration={planned:.4f},setpts=PTS-STARTPTS"
        time_args = ["-t", f"{planned:.4f}"]
    else:
        vf = scale
        time_args = []

    run_ffmpeg(
        [
            "-i",
            str(src),
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            *time_args,
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ]
    )
    fitted = probe_file(dest)
    return NormalizeResult(
        duration=float(fitted.get("duration") or out_dur),
        duration_mismatch=mismatch,
        planned_seconds=planned,
        actual_seconds=actual,
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
    """Combine shot clips + narration + SFX + music into one scene MP4.

    Video timeline follows real (normalized) clip lengths — no freeze-pad.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    if not inputs.shot_clips:
        raise ValueError("Scene render requires at least one shot clip")

    n = len(inputs.shot_clips)
    shot_plans = list(inputs.shot_target_durations or [])
    while len(shot_plans) < n:
        shot_plans.append(0.0)

    scene_plan = float(inputs.target_duration or 0.0)
    missing = [i for i, d in enumerate(shot_plans) if d <= 0]
    if missing and scene_plan > 0:
        known = sum(d for d in shot_plans if d > 0)
        remain = max(0.1, scene_plan - known)
        each = remain / len(missing)
        for i in missing:
            shot_plans[i] = each
    elif missing:
        for i in missing:
            probed = float(probe_file(inputs.shot_clips[i]).get("duration") or 1.0)
            shot_plans[i] = max(0.1, probed)

    normalized: list[Path] = []
    fitted_sum = 0.0
    mismatch_flags: list[bool] = []
    for index, clip in enumerate(inputs.shot_clips):
        norm = work_dir / f"shot_norm_{index}.mp4"
        result = _normalize_clip(
            src=clip,
            dest=norm,
            planned_seconds=shot_plans[index],
            width=width,
            height=height,
            fps=fps,
        )
        fitted_sum += result.duration
        mismatch_flags.append(result.duration_mismatch)
        normalized.append(norm)

    # Expose flags for callers that inspect work_dir sidecar (optional).
    flag_path = work_dir / "shot_duration_mismatch.json"
    try:
        import json

        flag_path.write_text(
            json.dumps(
                {
                    "mismatches": mismatch_flags,
                    "any": any(mismatch_flags),
                    "planned": shot_plans[:n],
                }
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

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

    video_duration = max(
        0.1, float(probe_file(video_only).get("duration") or fitted_sum)
    )

    audio_specs: list[tuple[Path, bool, str]] = []
    for path, loop, tag in (
        (inputs.narration, False, "narr"),
        (inputs.sfx, True, "sfx"),
        (inputs.music, True, "mus"),
    ):
        if path is not None:
            audio_specs.append((path, loop, tag))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Never use -shortest: audio must stretch to the video timeline.

    if not audio_specs:
        run_ffmpeg(
            [
                "-i",
                str(video_only),
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r=44100:cl=stereo:d={video_duration:.4f}",
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
                "-t",
                f"{video_duration:.4f}",
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
            + f";{mix_in}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=0,"
            f"atrim=0:{video_duration:.4f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={fade_seconds},"
            f"afade=t=out:st={max(0.0, video_duration - fade_seconds):.4f}:d={fade_seconds},"
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
                "-t",
                f"{video_duration:.4f}",
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
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-read_intervals",
                "0%+1",
                "-show_frames",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise FFmpegValidationError(f"Final output not playable: {proc.stderr.strip()}")
    return info
