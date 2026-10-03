"""FFmpeg scene composition and final assembly."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.media.ffmpeg_tools import run_ffmpeg
from app.media.ffprobe import probe_file

logger = logging.getLogger(__name__)

# Flag shot when |actual - planned| exceeds this (seconds). No freeze-pad.
DURATION_MISMATCH_SECONDS = 1.0

DEFAULT_MUSIC_VOLUME = 0.30
DEFAULT_SFX_VOLUME = 0.22
DEFAULT_VOICE_VOLUME = 1.0
DEFAULT_LOUDNORM_I = -16.0
DEFAULT_SCENE_CROSSFADE = 0.4


@dataclass(slots=True)
class VoiceLineInput:
    """One dialogue line placed on the scene timeline."""

    path: Path
    start_seconds: float
    duration_seconds: float = 0.0
    line_id: str = ""


@dataclass(slots=True)
class SceneInputs:
    shot_clips: list[Path]
    narration: Path | None = None  # legacy single-file fallback
    voice_lines: list[VoiceLineInput] = field(default_factory=list)
    sfx: Path | None = None
    music: Path | None = None
    # Planned scene length (seconds). Used for mismatch / overflow checks.
    target_duration: float | None = None
    # Optional per-shot plan lengths (same order as shot_clips).
    shot_target_durations: list[float] | None = None
    music_volume: float = DEFAULT_MUSIC_VOLUME
    sfx_volume: float = DEFAULT_SFX_VOLUME
    voice_volume: float = DEFAULT_VOICE_VOLUME
    loudnorm_i: float = DEFAULT_LOUDNORM_I


@dataclass(slots=True)
class NormalizeResult:
    duration: float
    duration_mismatch: bool = False
    planned_seconds: float = 0.0
    actual_seconds: float = 0.0


class FFmpegValidationError(RuntimeError):
    pass


def build_scene_mix_filter(
    *,
    voice_count: int,
    has_sfx: bool,
    has_music: bool,
    mix_duration: float,
    music_volume: float = DEFAULT_MUSIC_VOLUME,
    sfx_volume: float = DEFAULT_SFX_VOLUME,
    voice_volume: float = DEFAULT_VOICE_VOLUME,
    loudnorm_i: float = DEFAULT_LOUDNORM_I,
    voice_starts_ms: list[int] | None = None,
    fade_seconds: float = 0.25,
) -> str:
    """Build filter_complex for voice timeline + ducked music + loudnorm.

    Input layout: 0=video, 1..V=voice lines, then optional sfx, then optional music.
    Requires voice_count >= 1 (caller handles sfx/music-only separately).
    """
    if voice_count < 1:
        raise ValueError("build_scene_mix_filter requires at least one voice line")

    t = max(0.1, float(mix_duration))
    starts = list(voice_starts_ms or [0] * voice_count)
    while len(starts) < voice_count:
        starts.append(0)

    parts: list[str] = []
    voice_labels: list[str] = []
    idx = 1
    for i in range(voice_count):
        delay = max(0, int(starts[i]))
        lab = f"v{i}"
        parts.append(
            f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
            f"volume={float(voice_volume):.3f},"
            f"adelay={delay}|{delay},apad[{lab}]"
        )
        voice_labels.append(f"[{lab}]")
        idx += 1

    if voice_count == 1:
        parts.append(f"{voice_labels[0]}volume=1[voice]")
    else:
        mix_in = "".join(voice_labels)
        parts.append(
            f"{mix_in}amix=inputs={voice_count}:duration=longest:normalize=0,"
            f"atrim=0:{t:.4f},asetpts=PTS-STARTPTS[voice]"
        )

    parts.append("[voice]asplit=2[vo_mix][vo_sc]")
    mix_inputs = ["[vo_mix]"]
    n_mix = 1

    if has_sfx:
        parts.append(
            f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
            f"aloop=loop=-1:size=2e+09,atrim=0:{t:.4f},asetpts=PTS-STARTPTS,"
            f"volume={float(sfx_volume):.3f}[sfx]"
        )
        mix_inputs.append("[sfx]")
        n_mix += 1
        idx += 1

    if has_music:
        parts.append(
            f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
            f"aloop=loop=-1:size=2e+09,atrim=0:{t:.4f},asetpts=PTS-STARTPTS,"
            f"volume={float(music_volume):.3f},"
            f"afade=t=in:st=0:d={min(0.5, t / 4):.3f}[mu_raw]"
        )
        parts.append(
            "[mu_raw][vo_sc]sidechaincompress="
            "threshold=0.03:ratio=8:attack=20:release=300[mu_duck]"
        )
        mix_inputs.append("[mu_duck]")
        n_mix += 1
    else:
        parts.append("[vo_sc]anullsink")

    fade_out_st = max(0.0, t - float(fade_seconds))
    mix_in = "".join(mix_inputs)
    parts.append(
        f"{mix_in}amix=inputs={n_mix}:duration=first:dropout_transition=0:normalize=0,"
        f"atrim=0:{t:.4f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={float(fade_seconds):.3f},"
        f"afade=t=out:st={fade_out_st:.4f}:d={float(fade_seconds):.3f},"
        f"loudnorm=I={float(loudnorm_i):.1f}:TP=-1.5:LRA=11[aout]"
    )
    return ";".join(parts)


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
    """Combine shot clips + per-line voice + SFX + theme music into one scene MP4.

    Voice lines are placed on a timeline (never silently trimmed). If voice runs
    longer than the video, the scene is extended (tpad) and overflow is flagged.
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

    # Resolve voice lines (new path) or legacy single narration.
    voice_lines = list(inputs.voice_lines or [])
    if not voice_lines and inputs.narration is not None:
        narr_dur = float(probe_file(inputs.narration).get("duration") or 0.0)
        voice_lines = [
            VoiceLineInput(
                path=inputs.narration,
                start_seconds=0.0,
                duration_seconds=narr_dur,
                line_id="narration",
            )
        ]

    voice_end = 0.0
    for vl in voice_lines:
        dur = float(vl.duration_seconds or 0.0)
        if dur <= 0:
            dur = float(probe_file(vl.path).get("duration") or 0.01)
        voice_end = max(voice_end, float(vl.start_seconds) + dur)

    scene_target = scene_plan if scene_plan > 0 else video_duration
    overflow = bool(voice_lines) and voice_end > scene_target + 1e-6
    overflow_by = max(0.0, voice_end - scene_target) if overflow else 0.0
    # Never cut lines: extend mix (and video) to fit the full voice timeline.
    mix_duration = max(video_duration, voice_end, 0.1)

    overflow_path = work_dir / "voice_overflow.json"
    try:
        import json

        overflow_path.write_text(
            json.dumps(
                {
                    "overflow": overflow,
                    "overflow_seconds": round(overflow_by, 4),
                    "voice_end_seconds": round(voice_end, 4),
                    "scene_duration_seconds": round(scene_target, 4),
                    "mix_duration_seconds": round(mix_duration, 4),
                    "line_count": len(voice_lines),
                }
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    if overflow:
        logger.warning(
            "voice overflow scene_plan=%.3fs voice_end=%.3fs (+%.3fs) — "
            "extending mix; lines were NOT cut",
            scene_target,
            voice_end,
            overflow_by,
        )

    # Pad video if voice needs a longer timeline.
    video_for_mix = video_only
    if mix_duration > video_duration + 0.05:
        pad = mix_duration - video_duration
        padded = work_dir / "video_padded.mp4"
        run_ffmpeg(
            [
                "-i",
                str(video_only),
                "-vf",
                f"tpad=stop_mode=clone:stop_duration={pad:.4f}",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-t",
                f"{mix_duration:.4f}",
                str(padded),
            ]
        )
        video_for_mix = padded

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not voice_lines and inputs.sfx is None and inputs.music is None:
        run_ffmpeg(
            [
                "-i",
                str(video_for_mix),
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r=44100:cl=stereo:d={mix_duration:.4f}",
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
                f"{mix_duration:.4f}",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
    else:
        cmd = ["-i", str(video_for_mix)]
        starts_ms: list[int] = []
        for vl in voice_lines:
            cmd.extend(["-i", str(vl.path)])
            starts_ms.append(int(round(max(0.0, float(vl.start_seconds)) * 1000)))
        has_sfx = inputs.sfx is not None
        has_music = inputs.music is not None
        if has_sfx:
            cmd.extend(["-i", str(inputs.sfx)])
        if has_music:
            cmd.extend(["-i", str(inputs.music)])

        filter_complex = build_scene_mix_filter(
            voice_count=len(voice_lines),
            has_sfx=has_sfx,
            has_music=has_music,
            mix_duration=mix_duration,
            music_volume=inputs.music_volume,
            sfx_volume=inputs.sfx_volume,
            voice_volume=inputs.voice_volume,
            loudnorm_i=inputs.loudnorm_i,
            voice_starts_ms=starts_ms,
            fade_seconds=fade_seconds,
        )
        # When no voice lines, build_scene_mix_filter starts with anullsrc — that
        # needs to be a lavfi input, not referenced from missing indices.
        if not voice_lines:
            # Rebuild simpler path: sfx/music only with loudnorm.
            filter_parts: list[str] = []
            mix_labels: list[str] = []
            next_i = 1
            if has_sfx:
                filter_parts.append(
                    f"[{next_i}:a]aresample=44100,aformat=channel_layouts=stereo,"
                    f"aloop=loop=-1:size=2e+09,atrim=0:{mix_duration:.4f},"
                    f"asetpts=PTS-STARTPTS,volume={inputs.sfx_volume:.3f}[sfx]"
                )
                mix_labels.append("[sfx]")
                next_i += 1
            if has_music:
                filter_parts.append(
                    f"[{next_i}:a]aresample=44100,aformat=channel_layouts=stereo,"
                    f"aloop=loop=-1:size=2e+09,atrim=0:{mix_duration:.4f},"
                    f"asetpts=PTS-STARTPTS,volume={inputs.music_volume:.3f}[mus]"
                )
                mix_labels.append("[mus]")
            fade_out_st = max(0.0, mix_duration - fade_seconds)
            filter_complex = (
                ";".join(filter_parts)
                + f";{''.join(mix_labels)}amix=inputs={len(mix_labels)}:"
                f"duration=first:dropout_transition=0:normalize=0,"
                f"atrim=0:{mix_duration:.4f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={fade_seconds:.3f},"
                f"afade=t=out:st={fade_out_st:.4f}:d={fade_seconds:.3f},"
                f"loudnorm=I={inputs.loudnorm_i:.1f}:TP=-1.5:LRA=11[aout]"
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
                f"{mix_duration:.4f}",
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
    audio_crossfade_seconds: float = DEFAULT_SCENE_CROSSFADE,
) -> float:
    """Concatenate scenes with 0.4s audio (and matching video) crossfade."""
    if not scene_videos:
        raise ValueError("Final assembly requires at least one scene video")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    xfade = max(0.0, float(audio_crossfade_seconds))

    if len(scene_videos) == 1 or xfade <= 0:
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
        return float(probe_file(output_path)["duration"])

    # Chain xfade + acrossfade across all scenes.
    durations = [
        max(0.1, float(probe_file(p).get("duration") or 0.1)) for p in scene_videos
    ]
    cmd: list[str] = []
    for path in scene_videos:
        cmd.extend(["-i", str(path)])

    v_label = "[0:v]"
    a_label = "[0:a]"
    parts: list[str] = []
    running = durations[0]
    for i in range(1, len(scene_videos)):
        offset = max(0.0, running - xfade)
        v_out = f"[vx{i}]"
        a_out = f"[ax{i}]"
        parts.append(
            f"{v_label}[{i}:v]xfade=transition=fade:duration={xfade:.3f}:"
            f"offset={offset:.4f}{v_out}"
        )
        parts.append(
            f"{a_label}[{i}:a]acrossfade=d={xfade:.3f}:c1=tri:c2=tri{a_out}"
        )
        v_label = v_out
        a_label = a_out
        running = running + durations[i] - xfade

    filter_complex = ";".join(parts)
    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            v_label,
            "-map",
            a_label,
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
    run_ffmpeg(cmd)
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
