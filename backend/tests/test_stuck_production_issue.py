"""Stuck / stale in-progress jobs surface as a resume issue for the UI."""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta
from typing import Any

from app.orchestration.status import (
    build_stuck_production_issue,
    classify_job_issue,
    collect_project_issues,
    is_stale_in_progress_job,
    stale_seconds_for_job,
)


def test_video_stale_threshold_is_twelve_minutes() -> None:
    assert stale_seconds_for_job({"type": "video"}) == 12 * 60
    assert stale_seconds_for_job({"type": "image"}) == 8 * 60


def test_is_stale_in_progress_job_detects_orphan_generating() -> None:
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    fresh = {
        "type": "video",
        "status": "generating",
        "updated_at": now - timedelta(minutes=3),
    }
    stale = {
        "type": "video",
        "status": "generating",
        "updated_at": now - timedelta(minutes=13),
    }
    completed = {
        "type": "video",
        "status": "completed",
        "updated_at": now - timedelta(minutes=30),
    }
    assert is_stale_in_progress_job(fresh, now=now) is False
    assert is_stale_in_progress_job(stale, now=now) is True
    assert is_stale_in_progress_job(completed, now=now) is False


def test_build_stuck_production_issue_is_project_level() -> None:
    issue = build_stuck_production_issue(
        [
            {
                "type": "video",
                "status": "generating",
                "shot_id": "shot-1",
                "scene_id": "scene-1",
            },
            {
                "type": "video",
                "status": "generating",
                "shot_id": "shot-2",
                "scene_id": "scene-1",
            },
        ]
    )
    assert issue is not None
    assert issue["code"] == "stuck"
    assert issue["action"] == "resume_missing"
    assert issue["jobType"] == "production"
    assert issue["shotId"] is None
    assert issue["stuckJobCount"] == 2
    assert issue["stuckShotIds"] == ["shot-1", "shot-2"]
    assert "Resume from here" in issue["message"] or "stuck" in issue["message"].lower()


def test_build_stuck_production_issue_empty() -> None:
    assert build_stuck_production_issue([]) is None


def test_classify_skips_internal_sfx_markers() -> None:
    assert (
        classify_job_issue(
            {
                "type": "sfx",
                "status": "failed",
                "error": "sfx_fal_hang_skipped",
                "scene_id": "scene-2",
            }
        )
        is None
    )
    assert (
        classify_job_issue(
            {
                "type": "sfx",
                "status": "failed",
                "error": "sfx_skipped_for_assembly",
                "scene_id": "scene-1",
            }
        )
        is None
    )


def test_collect_hides_failed_video_when_later_success(monkeypatch: Any) -> None:
    jobs = [
        {
            "id": "v1",
            "type": "video",
            "status": "failed",
            "error": "All video providers failed: ['fal']",
            "error_class": "RETRYABLE",
            "shot_id": "shot-3",
            "scene_id": "scene-2",
        },
        {
            "id": "v2",
            "type": "video",
            "status": "completed",
            "shot_id": "shot-3",
            "scene_id": "scene-2",
        },
    ]

    class FakeSettings:
        mongodb_url = "mongodb://x"
        mongodb_db_name = "t"

    import app.core.config as cfg
    import app.workers.job_store as js

    monkeypatch.setattr(cfg, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(js, "get_db", lambda *_a, **_k: object())
    monkeypatch.setattr(js, "list_jobs_for_project", lambda *_a, **_k: jobs)

    pc = types.ModuleType("app.workers.project_context")
    pc.get_project_doc = lambda *_a, **_k: {"status": "producing"}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.workers.project_context", pc)

    assert collect_project_issues("proj") == []


def test_collect_empty_when_project_completed(monkeypatch: Any) -> None:
    class FakeSettings:
        mongodb_url = "mongodb://x"
        mongodb_db_name = "t"

    import app.core.config as cfg
    import app.workers.job_store as js

    monkeypatch.setattr(cfg, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(js, "get_db", lambda *_a, **_k: object())
    monkeypatch.setattr(
        js,
        "list_jobs_for_project",
        lambda *_a, **_k: [
            {
                "id": "v1",
                "type": "video",
                "status": "failed",
                "error": "boom",
                "shot_id": "shot-1",
            }
        ],
    )
    pc = types.ModuleType("app.workers.project_context")
    pc.get_project_doc = lambda *_a, **_k: {"status": "completed"}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.workers.project_context", pc)

    assert collect_project_issues("proj") == []
