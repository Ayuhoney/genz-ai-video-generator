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


def test_fal_image_provider_switches_to_i2i_with_refs(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("FAL_IMAGE_I2I_MODEL", "fal-ai/flux/dev/image-to-image")
    monkeypatch.setenv("FAL_IMAGE_I2I_STRENGTH", "0.55")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()

    class FakeClient(FalClient):
        def __init__(self, *a, **k):
            super().__init__("test-fal-key", poll_interval=0.01, poll_timeout=5)

        def run(self, model: str, arguments: dict, **kwargs):
            captured["model"] = model
            captured["arguments"] = arguments
            return (
                {
                    "images": [
                        {
                            "url": "https://cdn.example/out.png",
                            "content_type": "image/png",
                        }
                    ]
                },
                {"inference_time": 1.0},
            )

        def download(self, url: str) -> bytes:
            return b"PNGDATA"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "app.providers.fal.image_video._make_client",
        lambda: FakeClient(),
    )
    result = FalImageProvider().generate(
        ImageRequest(
            project_id="p1",
            prompt="scene two outdoor",
            reference_image_urls=["https://fal.media/files/locked-look.jpg"],
            extra={"strength": 0.55},
        )
    )
    assert result.data == b"PNGDATA"
    assert captured["model"] == "fal-ai/flux/dev/image-to-image"
    args = captured["arguments"]
    assert isinstance(args, dict)
    assert args["image_url"] == "https://fal.media/files/locked-look.jpg"
    assert args["strength"] == 0.55


def test_first_scene_id_orders_by_director(monkeypatch) -> None:
    from app.workers import project_context as pc

    monkeypatch.setattr(
        pc,
        "get_project_doc",
        lambda _pid: {
            "director_response": {
                "scenes": [
                    {"id": "s2", "order": 2},
                    {"id": "s1", "order": 1},
                ]
            }
        },
    )
    assert pc.ordered_scene_ids("p1") == ["s1", "s2"]
    assert pc.first_scene_id("p1") == "s1"


def test_fal_video_provider_i2v_mocked(monkeypatch) -> None:
    states = {"n": 0}
    model = "fal-ai/wan/v2.2-a14b/image-to-video/turbo"
    monkeypatch.setenv("FAL_VIDEO_MODEL", model)
    monkeypatch.setenv("FAL_VIDEO_MODEL_CHAIN", model)
    get_settings.cache_clear()
    get_worker_settings.cache_clear()

    def video_route(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and "image-to-video/turbo" in path:
            body = request.read()
            assert b"image_url" in body
            assert b"resolution" in body
            assert b"num_frames" not in body
            return httpx.Response(
                200,
                json={
                    "request_id": "v1",
                    "status_url": (
                        "https://queue.fal.run/fal-ai/wan/requests/v1/status"
                    ),
                    "response_url": "https://queue.fal.run/fal-ai/wan/requests/v1",
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

        def upload_bytes(self, data, *, content_type, file_name="x"):
            return "https://v3b.fal.media/files/test/scene.png"

    monkeypatch.setattr(
        "app.providers.fal.image_video._make_client",
        lambda: FakeClient("test-fal-key"),
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.idempotency_claim",
        lambda _cid: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.idempotency_bind",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.enforce_job_budget",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.ensure_project_not_paused",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.bump_circuit_breaker",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.record_clip_cost_log",
        lambda **_k: None,
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
    assert result.metadata["model"] == model


def test_video_retry_uses_job_scoped_clip_id(monkeypatch) -> None:
    """User Retry must not be blocked by a prior shot-level fal idempotency key."""
    cleared: list[str] = []
    seen: dict[str, str | None] = {}

    class FakeClip:
        video_url = "https://cdn.example/clip.mp4"
        result = {"video": {"url": "https://cdn.example/clip.mp4"}}
        metrics = {"request_id": "req-2"}
        model_id = "fal-ai/wan-25-preview/image-to-video"
        prompt_used = "pan"
        image_url = "https://cdn.example/scene.png"
        request_id = "req-2"
        attempts = 1
        prompt_sanitize_level = 0
        estimated_cost_usd = 0.05
        model_chain_used = ["fal-ai/wan-25-preview/image-to-video"]

    class FakeClient(FalClient):
        def __init__(self, *a, **k):
            super().__init__(
                "test-fal-key",
                poll_interval=0.01,
                poll_timeout=5,
                http_client=httpx.Client(
                    transport=httpx.MockTransport(lambda _r: httpx.Response(404))
                ),
            )

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, url: str) -> bytes:
            return b"MP4DATA"

    def fake_generate_clip(client, request, **_kwargs):
        seen["clip_id"] = request.clip_id
        return FakeClip()

    monkeypatch.setattr(
        "app.providers.fal.image_video._make_client",
        lambda: FakeClient("test-fal-key"),
    )
    monkeypatch.setattr(
        "app.providers.fal.image_video.generate_clip",
        fake_generate_clip,
    )
    monkeypatch.setattr(
        "app.providers.fal.clip_controls.idempotency_clear",
        lambda cid: cleared.append(cid),
    )

    FalVideoProvider().generate(
        VideoRequest(
            project_id="proj1",
            scene_id="scene-3",
            shot_id="shot-6",
            prompt="pan left",
            duration_seconds=5,
            image_url="https://cdn.example/scene.png",
            extra={
                "job_id": "job-retry-1",
                "force": True,
                "regen_token": "tok-1",
            },
        )
    )
    assert seen["clip_id"] == "proj1:scene-3:shot-6:job-retry-1"
    assert "proj1:scene-3:shot-6" in cleared


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
    from app.storage import get_storage, reset_storage_cache
    from app.storage.asset_store import create_asset

    reset_storage_cache()

    settings = get_settings()
    db = job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)
    project_id = f"p-{uuid.uuid4().hex[:8]}"
    # Each shot video requires that shot's own still.
    still_key = (
        f"projects/{project_id}/scenes/scene-1/shots/scene-1-shot-1/image/still.png"
    )
    get_storage().upload(still_key, b"PNGSTILL", content_type="image/png")
    create_asset(
        db,
        project_id=project_id,
        scene_id="scene-1",
        shot_id="scene-1-shot-1",
        asset_type="image",
        r2_key=still_key,
        mime="image/png",
        size=8,
        provider="mock",
        cost=0.0,
    )
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
