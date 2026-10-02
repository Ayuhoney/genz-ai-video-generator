"""Prompt softening keeps action story fidelity."""

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
    assert "gun" not in out
    # Safety appendix may say "no blood" / "no gore" — story body must not invent gore.
    story, _, safety = out.partition(". cinematic film still")
    assert "blood" not in story
    assert "no gore" in safety or "no gore" in out


def test_soft_visual_maps_fight_to_confrontation() -> None:
    out = soft_visual_prompt("warehouse fight with armed attackers").lower()
    assert "warehouse" in out
    assert "confrontation" in out or "opponent" in out
    assert "peaceful everyday" not in out


def test_motion_prompt_uses_scene_context_for_action() -> None:
    out = motion_only_prompt(ACTION_IDEA).lower()
    assert "warehouse" in out
    assert "tracking" in out or "dynamic" in out
    assert "peaceful everyday scene" not in out
    assert "family-friendly" not in out


def test_motion_prompt_gentle_for_non_action() -> None:
    out = motion_only_prompt("a quiet chai stall at dusk in mumbai").lower()
    assert "push-in" in out or "parallax" in out
    assert "chai" in out or "mumbai" in out
