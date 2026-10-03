"""Voice timeline placement + character voice map + mix filter."""

from __future__ import annotations

import pytest

from app.media.audio_timeline import (
    FEMALE_VOICES,
    MALE_VOICES,
    assign_lines_to_shots,
    build_character_voice_map,
    build_shot_linked_voice_timeline,
    build_shot_windows,
    build_voice_timeline,
    dialogue_lines_from_scene,
    infer_gender,
    resolve_character_voice,
)
from app.media.ffmpeg_service import assemble_final_video, build_scene_mix_filter
from app.media.ffmpeg_tools import require_ffmpeg_tools
from app.media.lavfi_fixtures import write_tiny_video_mp4


def test_timeline_places_three_lines_with_gap() -> None:
    """1 scene, 3 lines → starts at 0, after dur+0.3, …"""
    timeline = build_voice_timeline(
        [("l1", 1.0), ("l2", 1.5), ("l3", 0.8)],
        scene_duration_seconds=10.0,
        gap_seconds=0.3,
    )
    assert len(timeline.placements) == 3
    assert timeline.placements[0].start_seconds == 0.0
    assert timeline.placements[1].start_seconds == pytest.approx(1.3)
    assert timeline.placements[2].start_seconds == pytest.approx(3.1)
    assert timeline.total_voice_seconds == pytest.approx(3.9)
    assert timeline.overflow is False


def test_timeline_overflow_flag_when_voice_longer_than_scene() -> None:
    timeline = build_voice_timeline(
        [("a", 2.0), ("b", 2.0), ("c", 2.0)],
        scene_duration_seconds=5.0,
        gap_seconds=0.3,
    )
    # 2+0.3+2+0.3+2 = 6.6 > 5
    assert timeline.overflow is True
    assert timeline.overflow_seconds == pytest.approx(1.6)
    # Lines keep full durations — never shortened.
    assert all(p.duration_seconds >= 2.0 for p in timeline.placements)


def test_character_voice_map_is_stable_per_character() -> None:
    chars = [
        {"id": "c1", "name": "Rama", "description": "young man", "role": "hero"},
        {"id": "c2", "name": "Sita", "description": "young woman", "role": "heroine"},
    ]
    m1 = build_character_voice_map(chars)
    m2 = build_character_voice_map(chars)
    assert m1["c1"] == m2["c1"]
    assert m1["c2"] == m2["c2"]
    assert m1["c1"] != m1["c2"]
    assert resolve_character_voice(m1, character_name="Narrator") == "shubh"


def test_hindi_girl_names_get_female_voices() -> None:
    """Devanagari names + वाली descriptions must not fall back to male TTS."""
    chars = [
        {
            "id": "char-1",
            "name": "ऐशा",
            "description": "छोटी काली बालों वाली, आत्मविश्वासी और समूह की नेत्री।",
            "role": "Protagonist",
        },
        {
            "id": "char-2",
            "name": "मीरा",
            "description": "लंबी घुँघराली बालों वाली, जिज्ञासु।",
            "role": "Curious Friend",
        },
        {
            "id": "char-4",
            "name": "निशा",
            "description": "छोटी बिंदी, उज्ज्वल हँसी।",
            "role": "Optimistic Friend",
        },
    ]
    assert infer_gender(chars[0]["name"], chars[0]["description"], chars[0]["role"]) == "female"
    assert infer_gender("निशा", chars[2]["description"], chars[2]["role"]) == "female"
    mapping = build_character_voice_map(chars)
    assert mapping["char-1"] in FEMALE_VOICES
    assert mapping["char-2"] in FEMALE_VOICES
    assert mapping["char-4"] in FEMALE_VOICES
    assert mapping["char-1"] != mapping["char-2"]


def test_explicit_gender_overrides_name_heuristic() -> None:
    mapping = build_character_voice_map(
        [{"id": "c1", "name": "Alex", "description": "", "gender": "female"}]
    )
    assert mapping["c1"] in FEMALE_VOICES
    mapping_m = build_character_voice_map(
        [{"id": "c2", "name": "Sita", "description": "young woman", "gender": "male"}]
    )
    assert mapping_m["c2"] in MALE_VOICES


def test_dialogue_lines_from_scene_order() -> None:
    scene = {
        "id": "scene-1",
        "voiceOver": [
            {"id": "vo-1", "characterName": "A", "text": "one"},
            {"id": "vo-2", "characterId": "c2", "characterName": "B", "text": "two"},
            {"id": "vo-3", "text": "three", "shotId": "shot-2"},
        ],
    }
    lines = dialogue_lines_from_scene(scene)
    assert [line["id"] for line in lines] == ["vo-1", "vo-2", "vo-3"]
    assert lines[1]["character_id"] == "c2"
    assert lines[2]["shot_id"] == "shot-2"


def test_assign_lines_to_shots_one_to_one() -> None:
    mapping = assign_lines_to_shots(
        ["vo-1", "vo-2"],
        ["shot-1", "shot-2"],
    )
    assert mapping == {"vo-1": "shot-1", "vo-2": "shot-2"}


def test_assign_lines_honors_explicit_shot() -> None:
    mapping = assign_lines_to_shots(
        ["vo-1", "vo-2"],
        ["shot-1", "shot-2"],
        explicit={"vo-1": "shot-2"},
    )
    assert mapping["vo-1"] == "shot-2"
    assert mapping["vo-2"] == "shot-1"


def test_shot_linked_timeline_places_line_inside_shot_window() -> None:
    """2 shots × 5s, 2 lines → each line starts in its own shot (not both at t=0)."""
    windows = build_shot_windows([("shot-1", 5.0), ("shot-2", 5.0)])
    mapping = assign_lines_to_shots(["vo-1", "vo-2"], ["shot-1", "shot-2"])
    timeline = build_shot_linked_voice_timeline(
        [("vo-1", 1.2), ("vo-2", 1.5)],
        shot_windows=windows,
        line_shot_map=mapping,
        gap_seconds=0.3,
        shot_lead_in_seconds=0.1,
    )
    by_id = {p.line_id: p for p in timeline.placements}
    assert by_id["vo-1"].shot_id == "shot-1"
    assert by_id["vo-2"].shot_id == "shot-2"
    assert by_id["vo-1"].start_seconds == pytest.approx(0.1)
    # Second shot window starts at 5.0 → line at 5.1
    assert by_id["vo-2"].start_seconds == pytest.approx(5.1)
    assert timeline.overflow is False


def test_shot_linked_multi_lines_same_shot_keep_gap() -> None:
    windows = build_shot_windows([("shot-1", 8.0), ("shot-2", 8.0)])
    mapping = {"vo-1": "shot-1", "vo-2": "shot-1", "vo-3": "shot-2"}
    timeline = build_shot_linked_voice_timeline(
        [("vo-1", 1.0), ("vo-2", 1.0), ("vo-3", 1.0)],
        shot_windows=windows,
        line_shot_map=mapping,
        gap_seconds=0.3,
        shot_lead_in_seconds=0.0,
    )
    by_id = {p.line_id: p for p in timeline.placements}
    assert by_id["vo-1"].start_seconds == pytest.approx(0.0)
    assert by_id["vo-2"].start_seconds == pytest.approx(1.3)
    assert by_id["vo-3"].start_seconds == pytest.approx(8.0)


def test_mix_filter_contains_duck_loudnorm_low_sfx() -> None:
    fc = build_scene_mix_filter(
        voice_count=3,
        has_sfx=True,
        has_music=True,
        mix_duration=8.0,
        music_volume=0.30,
        sfx_volume=0.22,
        voice_starts_ms=[0, 1300, 3100],
        loudnorm_i=-16.0,
    )
    assert "adelay=0|0" in fc
    assert "adelay=1300|1300" in fc
    assert "adelay=3100|3100" in fc
    assert "sidechaincompress" in fc
    assert "loudnorm=I=-16.0" in fc
    assert "volume=0.220" in fc
    assert "volume=0.300" in fc


def test_assemble_uses_acrossfade(tmp_path) -> None:
    require_ffmpeg_tools()
    scenes = []
    for i in range(2):
        path = tmp_path / f"s{i}.mp4"
        write_tiny_video_mp4(path, duration=0.6)
        scenes.append(path)
    final = tmp_path / "final.mp4"
    # Spy via checking output duration shrinks by ~crossfade vs hard concat.
    total = assemble_final_video(
        scene_videos=scenes,
        output_path=final,
        audio_crossfade_seconds=0.4,
    )
    assert final.is_file()
    # 0.6+0.6-0.4 = 0.8 (approx); hard concat would be ~1.2
    assert total < 1.05
    assert total > 0.5
