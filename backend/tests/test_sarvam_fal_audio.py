"""Sarvam + fal audio providers (mocked HTTP)."""

from __future__ import annotations

import base64

import httpx
import pytest

from app.core.config import get_settings
from app.providers.base import MusicRequest, SFXRequest, TTSRequest
from app.providers.registry import reset_registry
from app.providers.sarvam.audio import SarvamTTSProvider
from app.workers.settings import get_worker_settings


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.setenv("PROVIDER_TTS", "mock")
    monkeypatch.setenv("PROVIDER_SFX", "mock")
    monkeypatch.setenv("PROVIDER_MUSIC", "mock")
    monkeypatch.setenv("SARVAM_KEY", "test-sarvam")
    monkeypatch.setenv("FAL_KEY", "test-fal")
    monkeypatch.setenv("FAL_SFX_MODEL", "test/sfx")
    monkeypatch.setenv("FAL_MUSIC_MODEL", "test/music")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    reset_registry()
    yield
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    reset_registry()


def test_sarvam_tts_mocked(monkeypatch) -> None:
    wav = b"RIFF....WAVEfmt "
    payload = {"audios": [base64.b64encode(wav).decode("ascii")]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("api-subscription-key") == "test-sarvam"
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "app.providers.sarvam.audio.SarvamClient",
        lambda key, timeout=60.0: __import__(
            "app.providers.sarvam.client", fromlist=["SarvamClient"]
        ).SarvamClient(key, timeout=timeout, http_client=httpx.Client(transport=transport)),
    )
    result = SarvamTTSProvider().generate(
        TTSRequest(project_id="p1", text="Namaste", language="Hindi")
    )
    assert result.provider_name == "sarvam"
    assert result.data == wav


def test_fal_music_mocked(monkeypatch) -> None:
    from app.providers.fal.audio import FalMusicProvider

    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def run(self, model, arguments):
            calls["n"] += 1
            assert model == "test/music"
            return {"audio_file": {"url": "https://cdn.example/m.wav"}}, {}

        def download(self, url):
            return b"music-bytes"

    monkeypatch.setattr("app.providers.fal.audio._make_client", lambda: FakeClient())
    result = FalMusicProvider().generate(
        MusicRequest(project_id="p1", prompt="calm score", duration_seconds=10)
    )
    assert result.data == b"music-bytes"
    assert calls["n"] == 1
