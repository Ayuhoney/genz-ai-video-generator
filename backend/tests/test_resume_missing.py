"""Resume / reuse: inventory + missing-only dispatch."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.workers.resume_missing import (
    ResumeInventory,
    ShotInventoryRow,
    confirmation_granted,
    estimate_missing_cost_usd,
    latest_successful_asset,
    run_resume_missing,
)


def test_latest_successful_prefers_shot_then_scene_image() -> None:
    assets = [
        {
            "type": "image",
            "scene_id": "scene-1",
            "shot_id": None,
            "r2_key": "scene.jpg",
            "job_id": "j1",
        },
        {
            "type": "image",
            "scene_id": "scene-1",
            "shot_id": "shot-1",
            "r2_key": "shot.jpg",
            "job_id": "j2",
        },
    ]
    storage = MagicMock()
    storage.exists.return_value = True

    import app.workers.resume_missing as mod

    orig = mod.get_storage
    mod.get_storage = lambda: storage  # type: ignore[assignment]
    try:
        hit = latest_successful_asset(
            assets,
            asset_type="image",
            ok_job_ids={"j1", "j2"},
            scene_id="scene-1",
            shot_id="shot-1",
            allow_scene_image_fallback=True,
        )
        assert hit is not None
        assert hit["r2_key"] == "shot.jpg"

        hit2 = latest_successful_asset(
            [
                {
                    "type": "image",
                    "scene_id": "scene-1",
                    "shot_id": None,
                    "r2_key": "scene.jpg",
                    "job_id": "j1",
                }
            ],
            asset_type="image",
            ok_job_ids={"j1"},
            scene_id="scene-1",
            shot_id="shot-2",
            allow_scene_image_fallback=True,
        )
        assert hit2 is not None
        assert hit2["r2_key"] == "scene.jpg"
    finally:
        mod.get_storage = orig  # type: ignore[assignment]


def test_estimate_cost_uses_settings(monkeypatch: Any) -> None:
    class S:
        fal_image_cost_usd = 0.003
        fal_video_cost_usd = 0.05
        sarvam_tts_cost_usd = 0.002
        fal_sfx_cost_usd = 0.006
        fal_music_cost_usd = 0.01

    monkeypatch.setattr(
        "app.workers.resume_missing.get_settings",
        lambda: S(),
    )
    assert estimate_missing_cost_usd(
        missing_images=2,
        missing_videos=1,
        missing_tts=1,
        missing_sfx=0,
        missing_music=1,
    ) == round(2 * 0.003 + 0.05 + 0.002 + 0.01, 6)


def test_confirmation_env_and_flag(monkeypatch: Any) -> None:
    monkeypatch.delenv("CONFIRM_RESUME", raising=False)
    assert confirmation_granted(confirm_flag=False) is False
    assert confirmation_granted(confirm_flag=True) is True
    monkeypatch.setenv("CONFIRM_RESUME", "true")
    assert confirmation_granted(confirm_flag=False) is True


def test_run_resume_requires_confirm(monkeypatch: Any) -> None:
    inv = ResumeInventory(
        project_id="p1",
        planned_shot_count=1,
        rows=[
            ShotInventoryRow(
                scene_id="scene-1",
                shot_id="shot-1",
                planned_text="hello",
                image_exists=True,
                clip_exists=False,
                tts_exists=True,
                sfx_exists=True,
                music_exists=True,
            )
        ],
        missing_video_shot_ids=["shot-1"],
        scenes=[{"id": "scene-1", "duration_seconds": 10, "title": "S"}],
        shots=[
            {
                "id": "shot-1",
                "scene_id": "scene-1",
                "description": "hello",
                "duration_seconds": 5,
            }
        ],
        estimated_cost_usd=0.05,
    )
    monkeypatch.setattr(
        "app.workers.resume_missing.build_inventory",
        lambda _pid: inv,
    )
    monkeypatch.setattr(
        "app.workers.resume_missing.format_inventory_table",
        lambda _i: "table",
    )
    monkeypatch.delenv("CONFIRM_RESUME", raising=False)
    out = run_resume_missing("p1", confirm=False)
    assert out["started"] is False
    assert out["estimated_cost_usd"] == 0.05


def test_run_resume_dispatches_only_missing(monkeypatch: Any) -> None:
    inv = ResumeInventory(
        project_id="p1",
        planned_shot_count=2,
        rows=[
            ShotInventoryRow(
                scene_id="scene-1",
                shot_id="shot-1",
                planned_text="a",
                image_exists=True,
                clip_exists=True,
                tts_exists=True,
                sfx_exists=True,
                music_exists=True,
            ),
            ShotInventoryRow(
                scene_id="scene-1",
                shot_id="shot-2",
                planned_text="b",
                image_exists=True,
                clip_exists=False,
                tts_exists=True,
                sfx_exists=True,
                music_exists=True,
            ),
        ],
        missing_video_shot_ids=["shot-2"],
        scenes=[{"id": "scene-1", "duration_seconds": 10, "title": "S"}],
        shots=[
            {
                "id": "shot-1",
                "scene_id": "scene-1",
                "description": "a",
                "duration_seconds": 5,
            },
            {
                "id": "shot-2",
                "scene_id": "scene-1",
                "description": "b",
                "duration_seconds": 5,
            },
        ],
        estimated_cost_usd=0.05,
    )
    calls: dict[str, Any] = {"video": [], "image": [], "asm": 0}

    monkeypatch.setattr(
        "app.workers.resume_missing.build_inventory",
        lambda _pid: inv,
    )
    monkeypatch.setattr(
        "app.workers.resume_missing.format_inventory_table",
        lambda _i: "table",
    )
    monkeypatch.setattr(
        "app.workers.resume_missing.confirmation_granted",
        lambda **_k: True,
    )

    class S:
        celery_task_always_eager = True
        fal_image_cost_usd = 0.003
        fal_video_cost_usd = 0.05
        sarvam_tts_cost_usd = 0.002
        fal_sfx_cost_usd = 0.006
        fal_music_cost_usd = 0.01

    monkeypatch.setattr("app.workers.resume_missing.get_settings", lambda: S())

    def fake_video(pid: str, shots: list[dict[str, Any]], **_k: Any) -> list[str]:
        calls["video"].extend([s["id"] for s in shots])
        return ["jv1"]

    def fake_image(pid: str, shots: list[dict[str, Any]], **_k: Any) -> list[str]:
        calls["image"].extend([s["id"] for s in shots])
        return ["ji1"]

    def fake_asm(*_a: Any, **_k: Any) -> list[str]:
        calls["asm"] += 1
        return ["ja1"]

    monkeypatch.setattr("app.workers.dispatch.dispatch_shot_video_jobs", fake_video)
    monkeypatch.setattr("app.workers.dispatch.dispatch_image_jobs", fake_image)
    monkeypatch.setattr("app.workers.dispatch.dispatch_assembly_jobs", fake_asm)
    monkeypatch.setattr(
        "app.workers.job_store.jobs_all_terminal",
        lambda *_a, **_k: True,
    )
    # After video dispatch, inventory still incomplete in this unit test — prevent
    # assemble by keeping clip missing on refresh.
    monkeypatch.setattr(
        "app.workers.resume_missing.get_sync_db_from_settings",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.workers.resume_missing.reclaim_in_progress_jobs",
        lambda *_a, **_k: [],
    )

    out = run_resume_missing("p1", confirm=True, assemble=True)
    assert out["started"] is True
    assert calls["video"] == ["shot-2"]
    assert calls["image"] == []
    # Still missing clip on refresh → no assemble yet
    assert calls["asm"] == 0
