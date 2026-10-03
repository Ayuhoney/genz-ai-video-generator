"""ALLOW_MOCK=false must block mock providers and project completion."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.providers.base import ImageRequest
from app.providers.mock import MockImageProvider
from app.workers import ffmpeg_render
from app.workers.settings import get_worker_settings


def test_allow_mock_false_blocks_provider_and_completion(monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_MOCK", "false")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()

    with pytest.raises(RuntimeError, match="ALLOW_MOCK=false"):
        MockImageProvider().generate(ImageRequest(project_id="p", prompt="x"))

    monkeypatch.setattr(ffmpeg_render, "_db", lambda: object())
    monkeypatch.setattr(
        ffmpeg_render,
        "list_assets_for_project",
        lambda _db, _pid: [{"provider": "mock", "type": "video"}],
    )
    with pytest.raises(RuntimeError, match="mock asset"):
        ffmpeg_render._mark_project_completed("proj-1", "asset-1", "r2/key")
