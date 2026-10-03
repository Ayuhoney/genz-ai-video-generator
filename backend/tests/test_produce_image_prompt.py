"""produce_image must never use kind=storyboard as the prompt."""

from __future__ import annotations

import pytest

from app.workers import produce as produce_mod


def test_produce_image_rejects_storyboard_kind(monkeypatch) -> None:
    monkeypatch.setattr(produce_mod, "enforce_budget", lambda _pid: None)
    monkeypatch.setattr(produce_mod, "_reuse_existing_asset", lambda **_k: None)
    monkeypatch.setattr(produce_mod, "first_scene_id", lambda _pid: None)
    monkeypatch.setattr(produce_mod, "_scene_visual_description", lambda *_a, **_k: "")
    monkeypatch.setattr(produce_mod, "character_bible_prompt", lambda _pid: "")
    monkeypatch.setattr(produce_mod, "locked_character_refs", lambda _pid: [])

    job = {
        "id": "job-1",
        "project_id": "proj-1",
        "scene_id": "scene-1",
        "input": {"scene_id": "scene-1", "kind": "storyboard"},
    }
    with pytest.raises(ValueError, match="Missing scene description"):
        produce_mod.produce_image(job)
