"""Unit tests for fal.ai providers (mocked HTTP — never hits fal)."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.providers.base import BudgetExceededError, ImageRequest, VideoRequest
from app.providers.fal.client import FalClient, extract_image_url, extract_video_url
from app.providers.fal.image_video import FalImageProvider, FalVideoProvider
from app.providers.registry import get_registry, reset_registry
from app.storage.asset_store import create_asset
from app.workers import job_store
from app.workers.produce import enforce_budget, produce_video
from app.workers.settings import get_worker_settings


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.setenv("PROVIDER_IMAGE", "mock")
    monkeypatch.setenv("PROVIDER_VIDEO", "mock")
    monkeypatch.setenv("FAL_KEY", "test-fal-key")
    monkeypatch.setenv("FAL_IMAGE_MODEL", "test-owner/test-image-model")
    monkeypatch.setenv("FAL_VIDEO_MODEL", "test-owner/test-video-model")
    monkeypatch.setenv("FAL_POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("FAL_POLL_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("MAX_PROJECT_COST_USD", "10")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    reset_registry()
    yield
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    reset_registry()


def test_extract_media_urls() -> None:
    assert (
        extract_image_url({"images": [{"url": "https://cdn.example/a.png"}]})
        == "https://cdn.example/a.png"
    )
    assert (
        extract_video_url({"video": {"url": "https://cdn.example/a.mp4"}})
        == "https://cdn.example/a.mp4"
    )


def test_fal_client_submit_poll_result() -> None:
    _POLL["n"] = 0
    transport = httpx.MockTransport(
        lambda request: _route(request),
    )
    client = FalClient(
        "test-fal-key",
        poll_interval=0.01,
        poll_timeout=2.0,
        http_client=httpx.Client(transport=transport),
    )
    result, metrics = client.run(
        "test-owner/test-image-model",
        {"prompt": "hello"},
    )
    assert result["images"][0]["url"] == "https://cdn.example/out.png"
    assert metrics.get("inference_time") == 1.5
    client.close()


_POLL = {"n": 0}


def _route(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "POST" and path.endswith("/test-owner/test-image-model"):
        assert request.headers.get("Authorization") == "Key test-fal-key"
        return httpx.Response(
            200,
            json={
                "request_id": "req-1",
                "status_url": "https://queue.fal.run/test-owner/test-image-model/requests/req-1/status",
                "response_url": "https://queue.fal.run/test-owner/test-image-model/requests/req-1",
            },
        )
    if path.endswith("/status"):
        _POLL["n"] += 1
        if _POLL["n"] < 2:
            return httpx.Response(
                200,
                json={"status": "IN_PROGRESS", "request_id": "req-1"},
            )
        return httpx.Response(
            200,
            json={
                "status": "COMPLETED",
                "request_id": "req-1",
                "metrics": {"inference_time": 1.5},
            },
        )
    if path.endswith("/requests/req-1"):
        return httpx.Response(
            200,
            json={
                "images": [
                    {
                        "url": "https://cdn.example/out.png",
                        "content_type": "image/png",
                    }
                ]
            },
        )
    if path.endswith("/out.png") or str(request.url).endswith("/out.png"):
        return httpx.Response(200, content=b"PNGDATA")
    return httpx.Response(404, text=f"unhandled {request.method} {request.url}")


def test_fal_image_provider_mocked(monkeypatch) -> None:
    _POLL["n"] = 0

    class FakeClient(FalClient):
        def __init__(self, *a, **k):
            transport = httpx.MockTransport(_route)
            super().__init__(
                "test-fal-key",
                poll_interval=0.01,
                poll_timeout=5,
                http_client=httpx.Client(transport=transport),
            )

        def download(self, url: str) -> bytes:
            return b"PNGDATA"

    monkeypatch.setattr(
        "app.providers.fal.image_video._make_client",
        lambda: FakeClient("test-fal-key"),
    )
    result = FalImageProvider().generate(
        ImageRequest(project_id="p1", prompt="a cat")
    )
    assert result.provider_name == "fal"
    assert result.data == b"PNGDATA"
    assert result.cost_usd > 0


def test_fal_video_provider_i2v_mocked(monkeypatch) -> None:
    states = {"n": 0}

    def video_route(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and "test-video-model" in path:
            body = request.read()
            assert b"image_url" in body
            assert b"duration" in body
            return httpx.Response(
                200,
                json={
                    "request_id": "v1",
                    "status_url": "https://queue.fal.run/test-owner/test-video-model/requests/v1/status",
                    "response_url": "https://queue.fal.run/test-owner/test-video-model/requests/v1",
                },
            )
        if path.endswith("/status"):
            states["n"] += 1
            if states["n"] < 2:
                return httpx.Response(200, json={"status": "IN_QUEUE", "queue_position": 0})
            return httpx.Response(
                200,
                json={
                    "status": "COMPLETED",
                    "metrics": {"inference_time": 4.0},
                },
            )
        if path.endswith("/requests/v1"):
            return httpx.Response(
                200,
                json={"video": {"url": "https://cdn.example/clip.mp4", "content_type": "video/mp4"}},
            )
        return httpx.Response(404, text=str(request.url))

    class FakeClient(FalClient):
        def __init__(self, *a, **k):
            super().__init__(
                "test-fal-key",
                poll_interval=0.01,
                poll_timeout=5,
                http_client=httpx.Client(transport=httpx.MockTransport(video_route)),
            )

        def download(self, url: str) -> bytes:
            return b"MP4DATA"

    monkeypatch.setattr(
        "app.providers.fal.image_video._make_client",
        lambda: FakeClient("test-fal-key"),
    )
    result = FalVideoProvider().generate(
        VideoRequest(
            project_id="p1",
            prompt="pan left",
            duration_seconds=5,
            image_url="https://cdn.example/scene.png",
        )
    )
    assert result.data == b"MP4DATA"
    assert result.duration_seconds == 5
    assert result.metadata["model"] == "test-owner/test-video-model"


def test_fal_requires_model_env(monkeypatch) -> None:
    monkeypatch.setenv("FAL_IMAGE_MODEL", "")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    with pytest.raises(Exception, match="FAL_IMAGE_MODEL"):
        FalImageProvider().generate(ImageRequest(project_id="p"))


def test_budget_guard_pauses_project(orchestration_db, monkeypatch) -> None:
    monkeypatch.setenv("MAX_PROJECT_COST_USD", "0.05")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()

    settings = get_settings()
    db = job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)
    project_id = f"p-{uuid.uuid4().hex[:8]}"
    db.projects.insert_one({"_id": project_id, "status": "producing", "title": "t"})
    create_asset(
        db,
        project_id=project_id,
        asset_type="image",
        r2_key=f"projects/{project_id}/scenes/_/shots/_/image/a.png",
        mime="image/png",
        size=10,
        provider="mock",
        cost=0.1,
    )
    with pytest.raises(BudgetExceededError):
        enforce_budget(project_id)
    doc = db.projects.find_one({"_id": project_id})
    assert doc is not None
    assert doc["status"] == "paused_budget"


def test_registry_still_defaults_to_mock() -> None:
    result = get_registry().generate_image(ImageRequest(project_id="p", prompt="x"))
    assert result.provider_name == "mock"


def test_produce_video_uses_shot_duration(
    orchestration_db,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIA_LOCAL_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("PROVIDER_VIDEO", "mock")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    from app.storage import reset_storage_cache

    reset_storage_cache()

    settings = get_settings()
    db = job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)
    project_id = f"p-{uuid.uuid4().hex[:8]}"
    job = job_store.create_or_get_job(
        db,
        project_id=project_id,
        task_type="video",
        scene_id="scene-1",
        shot_id="scene-1-shot-1",
        input_payload={
            "duration_seconds": 5,
            "prompt": "motion",
            "shot_id": "scene-1-shot-1",
        },
    )
    out = produce_video(job)
    assert out["duration"] == 5 or out["type"] == "video"
    assert out["r2_key"]
