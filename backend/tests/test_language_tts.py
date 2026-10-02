"""Language / spoken-narration helpers for TTS."""

from __future__ import annotations

from app.providers.sarvam.audio import language_to_sarvam_code
from app.workers.project_context import (
    fallback_spoken_line,
    join_voice_over_text,
    spoken_text_from_scene,
)


def test_language_to_sarvam_maps_hindi() -> None:
    assert language_to_sarvam_code("Hindi") == "hi-IN"
    assert language_to_sarvam_code("English (India)") == "en-IN"
    assert language_to_sarvam_code("Tamil") == "ta-IN"


def test_join_voice_over_prefers_spoken_lines() -> None:
    text = join_voice_over_text(
        [
            {"text": "यह मेरी कहानी है।"},
            {"text": "अब आगे बढ़ते हैं।"},
        ]
    )
    assert "कहानी" in text
    assert "आगे" in text


def test_spoken_text_ignores_english_visual_description() -> None:
    scene = {
        "id": "scene-1",
        "title": "Harbor",
        "description": "Wide foggy harbor at night with radio lights.",
        "voiceOver": [{"text": "रात का बंदरगाह शांत है।"}],
    }
    assert spoken_text_from_scene(scene) == "रात का बंदरगाह शांत है।"


def test_spoken_text_fallback_is_hindi_not_english_description() -> None:
    scene = {
        "id": "scene-1",
        "title": "Chai Stall",
        "description": "A busy Mumbai chai stall at dusk.",
    }
    text = spoken_text_from_scene(scene, language="Hindi")
    assert "कहानी" in text
    assert "Mumbai" not in text


def test_fallback_english_when_english_selected() -> None:
    line = fallback_spoken_line(language="English (India)", scene_title="Open")
    assert "story continues" in line.lower()
