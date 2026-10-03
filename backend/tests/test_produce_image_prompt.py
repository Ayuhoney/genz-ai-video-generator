"""produce_image must never use kind=storyboard as the prompt."""

from __future__ import annotations

import pytest

from app.workers import produce as produce_mod


def test_produce_image_rejects_storyboard_kind(monkeypatch) -> None:
    monkeypatch.setattr(produce_mod, "enforce_budget", lambda _pid: None)
    monkeypatch.setattr(produce_mod, "_reuse_existing_asset", lambda **_k: None)
    monkeypatch.setattr(produce_mod, "first_scene_id", lambda _pid: None)
    monkeypatch.setattr(produce_mod, "_scene_visual_description", lambda *_a, **_k: "")
    monkeypatch.setattr(produce_mod, "locale_visual_lock", lambda _pid: "")
    monkeypatch.setattr(produce_mod, "locked_character_refs", lambda _pid: [])
    monkeypatch.setattr(produce_mod, "estimate_images_cost_usd", lambda _n: 0.0)
    monkeypatch.setattr(produce_mod.job_store, "update_job", lambda *_a, **_k: None)

    job = {
        "id": "job-1",
        "project_id": "proj-1",
        "scene_id": "scene-1",
        "input": {"scene_id": "scene-1", "kind": "storyboard"},
    }
    monkeypatch.setattr(
        produce_mod,
        "_shot_visual_description",
        lambda *_a, **_k: "",
    )
    with pytest.raises(ValueError, match="Missing shot description"):
        produce_mod.produce_image(job)
