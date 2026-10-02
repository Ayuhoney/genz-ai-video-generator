"""Sarvam AI TTS client (Indian languages)."""

from __future__ import annotations

import base64
from typing import Any

import httpx

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"


class SarvamAPIError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SarvamClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 60.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not (api_key or "").strip():
            raise SarvamAPIError("SARVAM_KEY is required")
        self.api_key = api_key.strip()
        self._owns = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns:
            self._client.close()

    def __enter__(self) -> SarvamClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def text_to_speech(
        self,
        *,
        text: str,
        language_code: str,
        speaker: str,
        model: str = "bulbul:v3",
    ) -> bytes:
        payload: dict[str, Any] = {
            "text": (text or "")[:2500],
            "target_language_code": language_code,
            "speaker": speaker,
            "model": model,
        }
        response = self._client.post(
            SARVAM_TTS_URL,
            headers={
                "api-subscription-key": self.api_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if response.status_code >= 400:
            raise SarvamAPIError(
                f"sarvam tts failed ({response.status_code}): {response.text[:400]}",
                status_code=response.status_code,
            )
        data = response.json()
        audios = data.get("audios")
        if not audios:
            raise SarvamAPIError(f"sarvam tts missing audios: {list(data.keys())}")
        if isinstance(audios, list):
            joined = "".join(str(a) for a in audios)
        else:
            joined = str(audios)
        return base64.b64decode(joined)
