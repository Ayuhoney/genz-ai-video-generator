"""Regenerate force / skip asset reuse."""

from __future__ import annotations

from typing import Any

from app.workers.produce import _reuse_existing_asset


def test_reuse_skipped_when_force(monkeypatch: Any) -> None:
    called = {"list": 0}

    def boom(*_a: Any, **_k: Any) -> list[Any]:
        called["list"] += 1
        return [{"type": "video", "scene_id": "s1", "shot_id": "sh1", "r2_key": "k", "id": "a1"}]

    monkeypatch.setattr("app.workers.produce.list_assets_for_project", boom)
    monkeypatch.setattr(
        "app.workers.produce.get_sync_db_from_settings",
        lambda: object(),
    )

    assert (
        _reuse_existing_asset(
            project_id="p1",
            asset_type="video",
            scene_id="s1",
            shot_id="sh1",
            force=True,
        )
        is None
    )
    assert called["list"] == 0
