"""Prompt softening keeps scene text; only strips blocked words."""

from __future__ import annotations

from app.providers.fal.clip_timing import motion_only_prompt, soft_visual_prompt


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


def test_motion_prompt_keeps_scene_text() -> None:
    out = motion_only_prompt(ACTION_IDEA).lower()
    assert "warehouse" in out
    assert "fight" in out
    assert "journalist" in out
    assert "cinematic camera motion" in out
    assert "peaceful everyday scene" not in out
    assert "blood" not in out
    assert "gore" not in out


def test_motion_prompt_gentle_for_non_action() -> None:
    out = motion_only_prompt("a quiet chai stall at dusk in mumbai").lower()
    assert "chai" in out
    assert "mumbai" in out


def test_motion_prompt_idempotent() -> None:
    scene = (
        "Wide shot of throne room, king on throne, Rama kneeling, "
        "crowd cheering, Kaikeyi in shadows"
    )
    once = motion_only_prompt(scene)
    twice = motion_only_prompt(once)
    assert once == twice
    assert "throne" in once.lower()
