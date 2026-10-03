"""SFX/music fal clients must use the shorter audio poll budget."""

from __future__ import annotations

from typing import Any

from app.providers.fal import audio as audio_mod


def test_make_client_uses_audio_poll_timeout(monkeypatch: Any) -> None:
    class S:
        fal_key = "k"
        fal_poll_interval_seconds = 2.0
        fal_poll_timeout_seconds = 900.0
        fal_audio_poll_timeout_seconds = 180.0
        fal_webhook_url = None

    monkeypatch.setattr(audio_mod, "_fal_settings", lambda: S())
    client = audio_mod._make_client()
    try:
        assert client.poll_timeout == 180.0
    finally:
        client.close()


def test_make_client_audio_poll_capped_by_video_poll(monkeypatch: Any) -> None:
    class S:
        fal_key = "k"
        fal_poll_interval_seconds = 2.0
        fal_poll_timeout_seconds = 60.0
        fal_audio_poll_timeout_seconds = 180.0
        fal_webhook_url = None

    monkeypatch.setattr(audio_mod, "_fal_settings", lambda: S())
    client = audio_mod._make_client()
    try:
        assert client.poll_timeout == 60.0
    finally:
        client.close()
