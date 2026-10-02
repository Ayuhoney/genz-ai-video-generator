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
    # Planned scene length (seconds). When set, output is forced to this
    # duration (trim if longer, freeze-pad if shorter) so short fal clips
    # never shrink the final cut.
    target_duration: float | None = None
    # Optional per-shot plan lengths (same order as shot_clips).
    shot_target_durations: list[float] | None = None


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


def _fit_clip_to_duration(
    *,
    src: Path,
    dest: Path,
    target_seconds: float,
    width: int,
    height: int,
    fps: int,
) -> float:
    """Scale/fps normalize a clip and force it to exactly target_seconds.

    Short clips (e.g. fal WAN turbo ~5s) are freeze-padded; long clips trimmed.
    """
    target = max(0.1, float(target_seconds))
    info = probe_file(src)
    actual = float(info.get("duration") or 0.0)
    if actual <= 0:
        raise FFmpegValidationError(f"Shot clip has no duration: {src}")

    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p"
    )
    if actual + 0.08 >= target:
        # Trim to plan length.
        vf = f"{scale},trim=duration={target:.4f},setpts=PTS-STARTPTS"
    else:
        # Hold last frame until plan length (covers short fal turbo clips).
        pad = max(0.0, target - actual)
        vf = f"{scale},tpad=stop_mode=clone:stop_duration={pad:.4f}"

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
            "-t",
            f"{target:.4f}",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ]
    )
    fitted = probe_file(dest)
    return float(fitted.get("duration") or target)


def _resolve_scene_target(
    *,
    fitted_sum: float,
    plan_target: float | None,
) -> float:
    """Prefer director/plan duration; never shrink below fitted video sum."""
    plan = float(plan_target or 0.0)
    base = max(0.1, fitted_sum)
    if plan <= 0:
        return base
    # Plan wins when it exceeds (or matches) actual clips — pad/trim to plan.
    # If somehow plan is shorter than fitted clips we already trimmed per-shot,
    # keep the fitted sum so we don't drop content unexpectedly.
    if plan + 0.05 < base:
        return base
    return plan


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

    Video timeline is the master clock. Audio is padded/looped to match.
    Planned ``target_duration`` (and optional per-shot targets) force exact
    lengths so short generator clips cannot collapse the final cut.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    if not inputs.shot_clips:
        raise ValueError("Scene render requires at least one shot clip")

    n = len(inputs.shot_clips)
    shot_plans = list(inputs.shot_target_durations or [])
    while len(shot_plans) < n:
        shot_plans.append(0.0)

    # Distribute scene plan across shots when per-shot plans are missing.
    scene_plan = float(inputs.target_duration or 0.0)
    missing = [i for i, d in enumerate(shot_plans) if d <= 0]
    if missing and scene_plan > 0:
        known = sum(d for d in shot_plans if d > 0)
        remain = max(0.1, scene_plan - known)
        each = remain / len(missing)
        for i in missing:
            shot_plans[i] = each
    elif missing:
        # No plan: use each clip's native duration.
        for i in missing:
            probed = float(probe_file(inputs.shot_clips[i]).get("duration") or 1.0)
            shot_plans[i] = max(0.1, probed)

    normalized: list[Path] = []
    fitted_sum = 0.0
    for index, clip in enumerate(inputs.shot_clips):
        norm = work_dir / f"shot_norm_{index}.mp4"
        fitted_sum += _fit_clip_to_duration(
            src=clip,
            dest=norm,
            target_seconds=shot_plans[index],
            width=width,
            height=height,
            fps=fps,
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

    probed_concat = float(probe_file(video_only).get("duration") or fitted_sum)
    video_duration = _resolve_scene_target(
        fitted_sum=probed_concat,
        plan_target=inputs.target_duration,
    )

    # Final hard lock: if concat drifted from scene plan, fit once more.
    if abs(probed_concat - video_duration) > 0.12:
        locked = work_dir / "video_locked.mp4"
        _fit_clip_to_duration(
            src=video_only,
            dest=locked,
            target_seconds=video_duration,
            width=width,
            height=height,
            fps=fps,
        )
        video_only = locked

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
    # Quick decode sanity check (first second).
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
