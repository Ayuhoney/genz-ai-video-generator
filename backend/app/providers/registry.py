"""Provider registry: primary + fallbacks with retry/timeout."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Callable, TypeVar

from app.providers.base import (
    ImageProvider,
    ImageRequest,
    MusicProvider,
    MusicRequest,
    ProviderError,
    ProviderResult,
    SFXProvider,
    SFXRequest,
    TTSProvider,
    TTSRequest,
    VideoProvider,
    VideoRequest,
)
from app.providers.mock import (
    MockImageProvider,
    MockMusicProvider,
    MockSFXProvider,
    MockTTSProvider,
    MockVideoProvider,
)

T = TypeVar("T")


def _fal_image():
    from app.providers.fal import FalImageProvider

    return FalImageProvider()


def _fal_video():
    from app.providers.fal import FalVideoProvider

    return FalVideoProvider()


def _fal_sfx():
    from app.providers.fal import FalSFXProvider

    return FalSFXProvider()


def _fal_music():
    from app.providers.fal import FalMusicProvider

    return FalMusicProvider()


def _sarvam_tts():
    from app.providers.sarvam import SarvamTTSProvider

    return SarvamTTSProvider()


_IMAGE: dict[str, Callable[[], ImageProvider]] = {
    "mock": MockImageProvider,
    "fal": _fal_image,
}
_VIDEO: dict[str, Callable[[], VideoProvider]] = {
    "mock": MockVideoProvider,
    "fal": _fal_video,
}
_TTS: dict[str, Callable[[], TTSProvider]] = {
    "mock": MockTTSProvider,
    "sarvam": _sarvam_tts,
}
_SFX: dict[str, Callable[[], SFXProvider]] = {
    "mock": MockSFXProvider,
    "fal": _fal_sfx,
}
_MUSIC: dict[str, Callable[[], MusicProvider]] = {
    "mock": MockMusicProvider,
    "fal": _fal_music,
}


def _parse_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in value.split(",") if part.strip()]


def _chain(primary: str, fallbacks: str | list[str] | None) -> list[str]:
    primary_name = (primary or "").strip()
    if not primary_name:
        raise ProviderError(
            "Primary provider is empty — set PROVIDER_IMAGE/VIDEO/TTS/SFX/MUSIC",
            errors=["missing primary provider"],
        )
    names = [primary_name]
    for name in _parse_list(fallbacks):
        # Never silently degrade to mock in production chains.
        if name == "mock":
            continue
        if name not in names:
            names.append(name)
    return names


def call_with_retry(
    fn: Callable[[], T],
    *,
    timeout_seconds: float = 30.0,
    max_retries: int = 2,
    backoff_base: float = 0.25,
) -> T:
    last_error: Exception | None = None
    attempts = max(1, max_retries + 1)
    for attempt in range(attempts):
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(fn)
                return future.result(timeout=timeout_seconds)
        except FuturesTimeout as exc:
            last_error = TimeoutError(
                f"Provider call timed out after {timeout_seconds}s"
            )
            last_error.__cause__ = exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            retryable = getattr(exc, "retryable", True)
            if retryable is False:
                raise
            try:
                from app.providers.fal.client import is_content_blocked_error

                if is_content_blocked_error(exc):
                    raise
            except ImportError:
                pass
        if attempt < attempts - 1:
            time.sleep(backoff_base * (2**attempt))
    assert last_error is not None
    raise last_error


def _resolve_settings() -> tuple[dict[str, str], dict[str, str | None], float, int]:
    try:
        from app.core.config import get_settings

        s = get_settings()
        primaries = {
            "image": s.provider_image,
            "video": s.provider_video,
            "tts": s.provider_tts,
            "sfx": s.provider_sfx,
            "music": s.provider_music,
        }
        fallbacks = {
            "image": s.provider_image_fallbacks,
            "video": s.provider_video_fallbacks,
            "tts": s.provider_tts_fallbacks,
            "sfx": s.provider_sfx_fallbacks,
            "music": s.provider_music_fallbacks,
        }
        return primaries, fallbacks, s.provider_timeout_seconds, s.provider_max_retries
    except Exception:
        from app.workers.settings import get_worker_settings

        s = get_worker_settings()
        primaries = {
            "image": s.provider_image,
            "video": s.provider_video,
            "tts": s.provider_tts,
            "sfx": s.provider_sfx,
            "music": s.provider_music,
        }
        fallbacks = {
            "image": s.provider_image_fallbacks,
            "video": s.provider_video_fallbacks,
            "tts": s.provider_tts_fallbacks,
            "sfx": s.provider_sfx_fallbacks,
            "music": s.provider_music_fallbacks,
        }
        return primaries, fallbacks, s.provider_timeout_seconds, s.provider_max_retries


def _run_chain(
    kind: str,
    factories: dict[str, Callable[[], object]],
    invoke: Callable[[object], ProviderResult],
) -> ProviderResult:
    primaries, fallbacks, timeout, max_retries = _resolve_settings()
    names = _chain(primaries[kind], fallbacks[kind])
    errors: list[str] = []
    try:
        from app.core.config import get_settings

        fal_poll = float(get_settings().fal_poll_timeout_seconds)
        sarvam_timeout = float(getattr(get_settings(), "sarvam_timeout_seconds", 60) or 60)
    except Exception:
        from app.workers.settings import get_worker_settings

        ws = get_worker_settings()
        fal_poll = float(ws.fal_poll_timeout_seconds)
        sarvam_timeout = float(getattr(ws, "sarvam_timeout_seconds", 60) or 60)

    for name in names:
        factory = factories.get(name)
        if factory is None:
            errors.append(f"{name}: unknown provider")
            continue
        provider = factory()
        if name == "fal":
            effective_timeout = max(timeout, fal_poll + 30)
        elif name == "sarvam":
            effective_timeout = max(timeout, sarvam_timeout + 15)
        else:
            effective_timeout = timeout
        try:
            result = call_with_retry(
                lambda p=provider: invoke(p),
                timeout_seconds=effective_timeout,
                max_retries=max_retries,
            )
            if not result.provider_name:
                result.provider_name = getattr(provider, "name", name)
            return result
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    raise ProviderError(
        f"All {kind} providers failed: {names}",
        errors=errors,
    )


class ProviderRegistry:
    def generate_image(self, request: ImageRequest) -> ProviderResult:
        return _run_chain(
            "image",
            _IMAGE,
            lambda p: p.generate(request),  # type: ignore[attr-defined]
        )

    def generate_video(self, request: VideoRequest) -> ProviderResult:
        return _run_chain(
            "video",
            _VIDEO,
            lambda p: p.generate(request),  # type: ignore[attr-defined]
        )

    def generate_tts(self, request: TTSRequest) -> ProviderResult:
        return _run_chain(
            "tts",
            _TTS,
            lambda p: p.generate(request),  # type: ignore[attr-defined]
        )

    def generate_sfx(self, request: SFXRequest) -> ProviderResult:
        return _run_chain(
            "sfx",
            _SFX,
            lambda p: p.generate(request),  # type: ignore[attr-defined]
        )

    def generate_music(self, request: MusicRequest) -> ProviderResult:
        return _run_chain(
            "music",
            _MUSIC,
            lambda p: p.generate(request),  # type: ignore[attr-defined]
        )


_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None
