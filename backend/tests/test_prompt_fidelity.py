"""Image prompts keep scene text; Wan video prompts are motion-only."""

from __future__ import annotations

from app.providers.fal.clip_timing import (
    DEFAULT_SAFE_MOTION_PROMPT,
    motion_only_prompt,
    soft_visual_prompt,
    ultra_safe_motion_prompt,
)


ACTION_IDEA = (
    "A lone ex-special-forces fighter walks into an abandoned warehouse at night "
    "to rescue a kidnapped journalist. A group of armed criminals attacks him. "
    "A brutal close-quarters fight breaks out. He rescues the journalist and escapes "
    "as the warehouse explodes behind them."
)


def test_soft_visual_keeps_warehouse_and_rescue() -> None:
    out = soft_visual_prompt(ACTION_IDEA).lower()
    assert "warehouse" in out
    assert "rescue" in out or "journalist" in out
    assert "peaceful everyday life" not in out
    assert "family-friendly" not in out
    assert "fight" in out  # meaning preserved (not rewritten)
    assert "blood" not in out
    assert "gore" not in out


def test_softener_removes_blocked_keeps_scene_words() -> None:
    out = soft_visual_prompt("warehouse fight with blood and gore").lower()
    assert "warehouse" in out
    assert "fight" in out
    assert "blood" not in out
    assert "gore" not in out
    assert "confrontation" not in out  # no meaning-changing rewrite


def test_soft_visual_idempotent() -> None:
    scene = "Sunset at forest edge, Rama, Sita, Lakshmana walk into forest"
    once = soft_visual_prompt(scene)
    twice = soft_visual_prompt(once)
    assert once == twice
    assert once.lower().count("cinematic film still") == 1


def test_motion_prompt_drops_story_and_weapons() -> None:
    out = motion_only_prompt(ACTION_IDEA).lower()
    assert "warehouse" not in out
    assert "fight" not in out
    assert "journalist" not in out
    assert "sword" not in out
    assert "cinematic slow motion" in out
    assert "camera pans" in out


def test_motion_prompt_keeps_camera_cues() -> None:
    out = motion_only_prompt(
        "quiet moment",
        camera="slow push-in, soft golden hour light",
    ).lower()
    assert "push" in out
    assert "cinematic slow motion" in out
    assert "fight" not in out


def test_motion_prompt_gentle_for_short_camera_note() -> None:
    out = motion_only_prompt("slow pan left, dust in air").lower()
    assert "pan" in out
    assert "cinematic slow motion" in out


def test_ultra_safe_ignores_story() -> None:
    out = ultra_safe_motion_prompt(ACTION_IDEA)
    assert out == DEFAULT_SAFE_MOTION_PROMPT
    assert "warehouse" not in out.lower()


def test_motion_prompt_idempotent() -> None:
    once = motion_only_prompt("slow dolly forward")
    twice = motion_only_prompt(once)
    assert once == twice
    assert "cinematic slow motion" in once.lower()
