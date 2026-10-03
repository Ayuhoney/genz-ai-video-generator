"""FFmpeg scene compose + final assembly (lavfi fixtures)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.media.ffmpeg_service import (
    FFmpegValidationError,
    SceneInputs,
    assemble_final_video,
    render_scene_video,
    validate_final_output,
)
from app.media.ffmpeg_tools import require_ffmpeg_tools
from app.media.lavfi_fixtures import (
    tiny_audio_wav_bytes,
    write_tiny_audio_wav,
    write_tiny_video_mp4,
)


@pytest.fixture(scope="module", autouse=True)
def _require_ffmpeg() -> None:
    require_ffmpeg_tools()


def test_render_scene_with_audio_mix(tmp_path: Path) -> None:
    shot = tmp_path / "shot.mp4"
    write_tiny_video_mp4(shot, duration=0.5)
    narr = tmp_path / "narr.wav"
    write_tiny_audio_wav(narr, duration=0.8)
    sfx = tmp_path / "sfx.wav"
    write_tiny_audio_wav(sfx, duration=0.2)
    music = tmp_path / "music.wav"
    write_tiny_audio_wav(music, duration=0.3)

    out = tmp_path / "scene.mp4"
    duration = render_scene_video(
        work_dir=tmp_path / "work",
        inputs=SceneInputs(
            shot_clips=[shot],
            narration=narr,
            sfx=sfx,
            music=music,
            target_duration=0.5,
        ),
        output_path=out,
        width=320,
        height=180,
        fps=12,
    )
    assert out.is_file()
    assert duration > 0
    validate_final_output(out, expected_duration=duration, tolerance_seconds=1.0)


def test_render_scene_video_only_gets_silent_audio(tmp_path: Path) -> None:
    shot = tmp_path / "shot.mp4"
    write_tiny_video_mp4(shot, duration=0.35)
    out = tmp_path / "scene.mp4"
    duration = render_scene_video(
        work_dir=tmp_path / "work",
        inputs=SceneInputs(shot_clips=[shot]),
        output_path=out,
        width=320,
        height=180,
        fps=12,
    )
    validate_final_output(out, expected_duration=duration, tolerance_seconds=1.0)


def test_render_scene_does_not_freeze_pad_short_clip(tmp_path: Path) -> None:
    """Short clips keep native length — no tpad freeze to plan."""
    shot = tmp_path / "shot.mp4"
    write_tiny_video_mp4(shot, duration=0.4)
    narr = tmp_path / "narr.wav"
    write_tiny_audio_wav(narr, duration=0.25)

    work = tmp_path / "work"
    out = tmp_path / "scene.mp4"
    duration = render_scene_video(
        work_dir=work,
        inputs=SceneInputs(
            shot_clips=[shot],
            narration=narr,
            target_duration=2.0,
            shot_target_durations=[2.0],
        ),
        output_path=out,
        width=320,
        height=180,
        fps=12,
        fade_seconds=0.05,
    )
    assert out.is_file()
    # Output follows actual clip (~0.4s), not padded plan 2.0s.
    assert duration < 0.9
    flag = work / "shot_duration_mismatch.json"
    assert flag.is_file()
    assert '"any": true' in flag.read_text(encoding="utf-8")
    validate_final_output(out, expected_duration=duration, tolerance_seconds=0.5)


def test_assemble_and_validate_final(tmp_path: Path) -> None:
    scenes: list[Path] = []
    for index in range(2):
        path = tmp_path / f"scene_{index}.mp4"
        write_tiny_video_mp4(path, duration=0.5)
        scenes.append(path)

    final = tmp_path / "final.mp4"
    total = assemble_final_video(
        scene_videos=scenes,
        output_path=final,
        audio_crossfade_seconds=0.4,
    )
    validate_final_output(final, expected_duration=total, tolerance_seconds=1.5)


def test_render_scene_mix_filter_has_loudnorm_and_duck() -> None:
    from app.media.ffmpeg_service import build_scene_mix_filter

    fc = build_scene_mix_filter(
        voice_count=1,
        has_sfx=True,
        has_music=True,
        mix_duration=5.0,
        sfx_volume=0.22,
        music_volume=0.30,
        loudnorm_i=-16.0,
    )
    assert "sidechaincompress" in fc
    assert "loudnorm=I=-16.0" in fc
    assert "volume=0.220" in fc


def test_validate_rejects_missing_audio(tmp_path: Path) -> None:
    from app.media.ffmpeg_tools import run_ffmpeg

    bad = tmp_path / "bad.mp4"
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=12:duration=0.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(bad),
        ]
    )
    with pytest.raises(FFmpegValidationError):
        validate_final_output(bad, expected_duration=0.2, tolerance_seconds=0.5)


def test_lavfi_audio_bytes() -> None:
    data = tiny_audio_wav_bytes(duration=0.15)
    assert len(data) > 44
