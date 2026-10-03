"""One image per planned shot + missing-shot check before assembly."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import get_settings
from app.workers import job_store
from app.workers.dispatch import dispatch_image_jobs, _shot_image_prompt
from app.workers.ffmpeg_render import (
    assert_planned_shots_have_clips,
    missing_planned_shot_ids,
)
from app.workers.produce import estimate_images_cost_usd
from app.workers.settings import get_worker_settings


def _db():
    settings = get_settings()
    db = job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)
    job_store.ensure_job_indexes(db)
    return db


def test_shot_image_prompt_uses_title_description_camera() -> None:
    prompt = _shot_image_prompt(
        {
            "title": "Wide establish",
            "description": "Rainy street at dusk",
            "camera": "slow push-in",
        }
    )
    assert "Wide establish" in prompt
    assert "Rainy street at dusk" in prompt
    assert "slow push-in" in prompt


def test_dispatch_image_jobs_one_per_shot(orchestration_db, monkeypatch) -> None:
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()

    project_id = f"p-{uuid.uuid4().hex[:8]}"
    shots = [
        {
            "id": "scene-1-shot-1",
            "scene_id": "scene-1",
            "title": "Wide",
            "description": "Plaza wide",
            "camera": "static",
        },
        {
            "id": "scene-1-shot-2",
            "scene_id": "scene-1",
            "title": "Medium",
            "description": "Hero walk",
            "camera": "track left",
        },
        {
            "id": "scene-1-shot-3",
            "scene_id": "scene-1",
            "title": "Close",
            "description": "Face CU",
            "camera": "slow dolly",
        },
        {
            "id": "scene-1-shot-4",
            "scene_id": "scene-1",
            "title": "Insert",
            "description": "Hands detail",
            "camera": "macro",
        },
    ]
    job_ids = dispatch_image_jobs(project_id, shots)
    assert len(job_ids) == 4
    db = _db()
    jobs = [job_store.get_job(db, jid) for jid in job_ids]
    assert all(j is not None for j in jobs)
    shot_ids = {j["shot_id"] for j in jobs if j}
    assert shot_ids == {s["id"] for s in shots}
    # Each job stores its own shot prompt (not a shared scene description).
    prompts = {(j.get("input") or {}).get("prompt") for j in jobs if j}
    assert len(prompts) == 4
    for job, shot in zip(
        sorted(jobs, key=lambda j: str((j or {}).get("shot_id"))),
        sorted(shots, key=lambda s: s["id"]),
        strict=True,
    ):
        assert job is not None
        assert job["scene_id"] == "scene-1"
        assert shot["title"] in str((job.get("input") or {}).get("prompt") or "")
        assert (job.get("input") or {}).get("shot_id") == shot["id"]


def test_estimate_images_cost_is_shots_times_unit(monkeypatch) -> None:
    monkeypatch.setenv("FAL_IMAGE_COST_USD", "0.01")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    assert estimate_images_cost_usd(4) == pytest.approx(0.04)
    assert estimate_images_cost_usd(0) == 0.0


def test_missing_planned_shot_ids_detects_gaps() -> None:
    planned = ["s1-shot-1", "s1-shot-2", "s1-shot-3", "s1-shot-4"]
    produced = ["s1-shot-1", "s1-shot-3"]
    missing = missing_planned_shot_ids(planned, produced)
    assert missing == ["s1-shot-2", "s1-shot-4"]


def test_assert_planned_shots_have_clips_fails_loudly() -> None:
    with pytest.raises(ValueError, match="Missing video clips for 2 planned shot"):
        assert_planned_shots_have_clips(
            scene_id="scene-1",
            planned_shot_ids=[
                "scene-1-shot-1",
                "scene-1-shot-2",
                "scene-1-shot-3",
                "scene-1-shot-4",
            ],
            produced_shot_ids={"scene-1-shot-1", "scene-1-shot-2"},
        )


def test_assert_planned_shots_have_clips_ok_when_complete() -> None:
    assert_planned_shots_have_clips(
        scene_id="scene-1",
        planned_shot_ids=["a", "b"],
        produced_shot_ids=["b", "a"],
    )
