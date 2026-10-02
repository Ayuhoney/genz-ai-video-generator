"""Sarvam TTS provider."""

from __future__ import annotations

from typing import Any

from app.providers.base import ProviderResult, TTSProvider, TTSRequest
from app.providers.sarvam.client import SarvamClient


def _settings() -> Any:
    try:
        from app.core.config import get_settings

        return get_settings()
    except Exception:
        from app.workers.settings import get_worker_settings

        return get_worker_settings()


def language_to_sarvam_code(language: str | None, language_code: str | None = None) -> str:
    if language_code and "-" in language_code:
        return language_code
    key = (language or language_code or "hi").strip().lower()
    mapping = {
        "hindi": "hi-IN",
        "hi": "hi-IN",
        "hi-in": "hi-IN",
        "english": "en-IN",
        "english (india)": "en-IN",
        "english india": "en-IN",
        "en": "en-IN",
        "en-in": "en-IN",
        "bengali": "bn-IN",
        "bangla": "bn-IN",
        "bn": "bn-IN",
        "gujarati": "gu-IN",
        "gu": "gu-IN",
        "kannada": "kn-IN",
        "kn": "kn-IN",
        "malayalam": "ml-IN",
        "ml": "ml-IN",
        "marathi": "mr-IN",
        "mr": "mr-IN",
        "odia": "od-IN",
        "oriya": "od-IN",
        "od": "od-IN",
        "or": "od-IN",
        "punjabi": "pa-IN",
        "pa": "pa-IN",
        "tamil": "ta-IN",
        "ta": "ta-IN",
        "telugu": "te-IN",
        "te": "te-IN",
    }
    return mapping.get(key, "hi-IN")


def default_speaker(language_code: str, voice: str | None = None) -> str:
    if voice and voice not in {"default", ""}:
        return voice
    if language_code.startswith("hi"):
        return "shubh"
    return "shubh"


class SarvamTTSProvider(TTSProvider):
    name = "sarvam"

    def generate(self, request: TTSRequest) -> ProviderResult:
        s = _settings()
        key = (getattr(s, "sarvam_key", None) or "").strip()
        if not key:
            raise RuntimeError("SARVAM_KEY is required when PROVIDER_TTS=sarvam")
        lang = language_to_sarvam_code(request.language, request.language_code)
        speaker = default_speaker(lang, request.voice)
        model = (request.model_id or getattr(s, "sarvam_tts_model", None) or "bulbul:v3").strip()
        timeout = float(getattr(s, "sarvam_timeout_seconds", 60) or 60)
        cost = float(getattr(s, "sarvam_tts_cost_usd", 0.002) or 0.002)

        with SarvamClient(key, timeout=timeout) as client:
            data = client.text_to_speech(
                text=request.text or " ",
                language_code=lang,
                speaker=speaker,
                model=model,
            )

        return ProviderResult(
            data=data,
            mime_type="audio/wav",
            filename="voice.wav",
            metadata={
                "language_code": lang,
                "speaker": speaker,
                "model": model,
                "chars": len(request.text or ""),
            },
            cost_usd=cost,
            provider_name=self.name,
        )
