"""Provider registry + storage (R2 mocked / local) tests."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from app.providers.base import ImageRequest, ProviderError
from app.providers.mock import MockImageProvider
from app.providers.registry import call_with_retry, get_registry, reset_registry
from app.storage import build_r2_key, create_storage, reset_storage_cache
from app.storage.asset_store import create_asset, get_asset, sum_cost_for_project
from app.storage.local import LocalStorage
from app.storage.r2 import R2Storage
from app.workers import job_store
from app.workers.dispatch import create_job, dispatch_jobs
from app.workers.produce import produce_image
from app.core.config import get_settings
from app.workers.settings import get_worker_settings


@pytest.fixture(autouse=True)
def _reset_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_LOCAL_ROOT", str(tmp_path / "media"))
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("R2_BUCKET", raising=False)
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    reset_storage_cache()
    reset_registry()
    yield
    reset_storage_cache()
    reset_registry()
    get_settings.cache_clear()
    get_worker_settings.cache_clear()


def test_build_r2_key_convention() -> None:
    key = build_r2_key(
        project_id="p1",
        scene_id="s1",
        shot_id="sh1",
        asset_type="video",
        filename="clip.mp4",
    )
    assert key == "projects/p1/scenes/s1/shots/sh1/video/clip.mp4"


def test_local_storage_upload_download_delete_presign(tmp_path) -> None:
    storage = LocalStorage(tmp_path / "media")
    key = "projects/p/scenes/_/shots/_/image/x.png"
    storage.upload(key, b"hello", content_type="image/png")
    assert storage.exists(key)
    assert storage.download(key) == b"hello"
    url = storage.presigned_url(key, expires_in=60)
    assert "expires=" in url
    storage.delete(key)
    assert not storage.exists(key)


def test_r2_storage_uses_mocked_boto3_client() -> None:
    client = MagicMock()
    storage = R2Storage(bucket="test-bucket", client=client)
    storage.upload("k", b"data", content_type="text/plain")
    client.put_object.assert_called_once()
    assert client.put_object.call_args.kwargs["Bucket"] == "test-bucket"
    assert client.put_object.call_args.kwargs["Key"] == "k"

    body = MagicMock()
    body.read.return_value = b"data"
    client.get_object.return_value = {"Body": body}
    assert storage.download("k") == b"data"

    client.generate_presigned_url.return_value = "https://signed.example/k"
    assert storage.presigned_url("k", expires_in=120) == "https://signed.example/k"
    client.generate_presigned_url.assert_called_once()
    assert client.generate_presigned_url.call_args.kwargs["ExpiresIn"] == 120

    storage.delete("k")
    client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="k")


def test_create_storage_falls_back_to_local_without_r2(tmp_path) -> None:
    storage = create_storage(media_local_root=str(tmp_path / "m"))
    assert isinstance(storage, LocalStorage)


def test_create_storage_uses_r2_when_configured() -> None:
    with patch("app.storage.r2.build_r2_client") as build:
        build.return_value = MagicMock()
        storage = create_storage(
            account_id="acct",
            access_key_id="key",
            secret_access_key="secret",
            bucket="bucket",
        )
    assert isinstance(storage, R2Storage)


def test_mock_provider_and_retry_wrapper() -> None:
    result = MockImageProvider().generate(ImageRequest(project_id="p", prompt="x"))
    assert result.data and result.cost_usd > 0
    assert result.mime_type == "image/png"

    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("boom")
        return "ok"

    assert call_with_retry(flaky, timeout_seconds=5, max_retries=2, backoff_base=0) == "ok"


def test_registry_fallback_on_failure(monkeypatch) -> None:
    """Mock is intentionally excluded from fallback chains — only real providers retry."""

    class BadImage(MockImageProvider):
        name = "bad"

        def generate(self, request: ImageRequest):
            raise RuntimeError("primary failed")

    class OkImage(MockImageProvider):
        name = "ok"

        def generate(self, request: ImageRequest):
            return super().generate(request)

    from app.providers import registry as reg

    monkeypatch.setitem(reg._IMAGE, "bad", BadImage)
    monkeypatch.setitem(reg._IMAGE, "ok", OkImage)
    monkeypatch.setenv("PROVIDER_IMAGE", "bad")
    # "mock" in fallbacks is skipped; a real secondary provider should still run.
    monkeypatch.setenv("PROVIDER_IMAGE_FALLBACKS", "mock,ok")
    monkeypatch.setenv("ALLOW_MOCK", "true")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    reset_registry()

    result = get_registry().generate_image(ImageRequest(project_id="p"))
    assert result.provider_name == "ok"


def test_registry_all_fail(monkeypatch) -> None:
    class BadImage(MockImageProvider):
        name = "bad"

        def generate(self, request: ImageRequest):
            raise RuntimeError("nope")

    from app.providers import registry as reg

    monkeypatch.setitem(reg._IMAGE, "bad", BadImage)
    monkeypatch.setenv("PROVIDER_IMAGE", "bad")
    monkeypatch.setenv("PROVIDER_IMAGE_FALLBACKS", "")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    reset_registry()

    with pytest.raises(ProviderError):
        get_registry().generate_image(ImageRequest(project_id="p"))


def test_produce_image_records_asset_and_cost(orchestration_db, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEDIA_LOCAL_ROOT", str(tmp_path / "media"))
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    reset_storage_cache()

    settings = get_settings()
    db = job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)
    project_id = f"p-{uuid.uuid4().hex[:8]}"
    job = job_store.create_or_get_job(
        db,
        project_id=project_id,
        task_type="image",
        scene_id="scene-1",
        input_payload={
            "scene_id": "scene-1",
            "prompt": "Rainy chai stall at dusk, warm bulbs, lonely bench",
        },
    )
    output = produce_image(job)
    assert output["r2_key"].startswith(f"projects/{project_id}/")
    assert output["cost"] > 0
    asset = get_asset(db, output["asset_id"])
    assert asset is not None
    assert asset["type"] == "image"
    assert "data" not in asset
    assert sum_cost_for_project(db, project_id) == asset["cost"]
    # Object landed in local media
    storage = create_storage(media_local_root=str(tmp_path / "media"))
    assert storage.exists(output["r2_key"])


def test_worker_image_task_uploads(orchestration_db, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEDIA_LOCAL_ROOT", str(tmp_path / "media"))
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    reset_storage_cache()

    project_id = f"p-{uuid.uuid4().hex[:8]}"
    job = create_job(
        project_id=project_id,
        task_type="image",
        scene_id="scene-1",
        input_payload={
            "scene_id": "scene-1",
            "prompt": "Rainy chai stall at dusk, warm bulbs, lonely bench",
        },
    )
    dispatch_jobs([job])
    settings = get_settings()
    db = job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)
    saved = job_store.get_job(db, job["id"])
    assert saved is not None
    assert saved["status"] in {"succeeded", "completed"}
    assert saved["output"]["r2_key"]
    assert saved.get("cost") is not None


@pytest.mark.asyncio
async def test_signed_asset_url_endpoint(
    client: AsyncClient,
    auth_headers_factory,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIA_LOCAL_ROOT", str(tmp_path / "media"))
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    reset_storage_cache()

    register = await client.post(
        "/api/auth/register",
        json={
            "email": f"{uuid.uuid4().hex[:8]}@example.com",
            "password": "secret123",
            "name": "Owner",
        },
    )
    headers = auth_headers_factory(register.json()["accessToken"])
    created = await client.post(
        "/api/projects",
        headers=headers,
        json={"title": "Assets", "durationSeconds": 30, "status": "draft"},
    )
    project_id = created.json()["id"]

    settings = get_settings()
    db = job_store.get_db(settings.mongodb_url, settings.mongodb_db_name)
    storage = create_storage(media_local_root=str(tmp_path / "media"))
    key = build_r2_key(
        project_id=project_id,
        scene_id="s1",
        shot_id=None,
        asset_type="image",
        filename="x.png",
    )
    storage.upload(key, b"png-bytes", content_type="image/png")
    asset = create_asset(
        db,
        project_id=project_id,
        asset_type="image",
        r2_key=key,
        mime="image/png",
        size=9,
        provider="mock",
        cost=0.001,
        scene_id="s1",
    )

    forbidden = await client.get(
        f"/api/projects/{project_id}/assets/{asset['id']}/url",
    )
    assert forbidden.status_code == 401

    ok = await client.get(
        f"/api/projects/{project_id}/assets/{asset['id']}/url",
        headers=headers,
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["assetId"] == asset["id"]
    assert "url" in body
    assert body["r2Key"] == key
