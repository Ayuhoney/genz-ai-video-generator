"""Abstract generation providers and typed results."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProviderResult:
    """Result of a provider call. Prefer `data` bytes; `url` is optional."""

    data: bytes | None
    mime_type: str
    filename: str
    metadata: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    provider_name: str = ""
    duration_seconds: float | None = None
    url: str | None = None

    def require_bytes(self) -> bytes:
        if self.data is not None:
            return self.data
        raise ValueError(f"Provider {self.provider_name!r} returned no bytes")


@dataclass(slots=True)
class ImageRequest:
    project_id: str
    scene_id: str | None = None
    prompt: str = ""
    width: int = 512
    height: int = 512
    reference_image_urls: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VideoRequest:
    project_id: str
    scene_id: str | None = None
    shot_id: str | None = None
    prompt: str = ""
    duration_seconds: float = 2.0
    image_url: str | None = None
    image_bytes: bytes | None = None
    image_mime: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class BudgetExceededError(Exception):
    """Raised when project spend would exceed MAX_PROJECT_COST_USD."""

    def __init__(self, project_id: str, spent: float, limit: float) -> None:
        self.project_id = project_id
        self.spent = spent
        self.limit = limit
        super().__init__(
            f"Project {project_id} budget exceeded: spent={spent:.4f} limit={limit:.4f}"
        )


@dataclass(slots=True)
class TTSRequest:
    project_id: str
    scene_id: str | None = None
    text: str = ""
    voice: str = "default"
    voice_id: str | None = None
    model_id: str | None = None
    language: str | None = None
    language_code: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SFXRequest:
    project_id: str
    scene_id: str | None = None
    prompt: str = ""
    duration_seconds: float = 1.0
    genre: str | None = None
    model_id: str | None = None
    loop: bool = False
    video_url: str | None = None
    video_bytes: bytes | None = None
    video_mime: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MusicRequest:
    project_id: str
    scene_id: str | None = None
    prompt: str = ""
    duration_seconds: float = 30.0
    music_length_ms: int | None = None
    genre: str | None = None
    model_id: str | None = None
    force_instrumental: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """Raised when all providers in the chain fail."""

    def __init__(
        self,
        message: str,
        *,
        errors: list[str] | None = None,
        error_class: str | None = None,
        fal_request_id: str | None = None,
        fal_calls: int | None = None,
    ) -> None:
        super().__init__(message)
        self.errors = errors or []
        self.error_class = error_class
        self.fal_request_id = fal_request_id
        self.fal_calls = fal_calls


class ImageProvider(ABC):
    name: str = "image"

    @abstractmethod
    def generate(self, request: ImageRequest) -> ProviderResult:
        ...


class VideoProvider(ABC):
    name: str = "video"

    @abstractmethod
    def generate(self, request: VideoRequest) -> ProviderResult:
        ...


class TTSProvider(ABC):
    name: str = "tts"

    @abstractmethod
    def generate(self, request: TTSRequest) -> ProviderResult:
        ...


class SFXProvider(ABC):
    name: str = "sfx"

    @abstractmethod
    def generate(self, request: SFXRequest) -> ProviderResult:
        ...


class MusicProvider(ABC):
    name: str = "music"

    @abstractmethod
    def generate(self, request: MusicRequest) -> ProviderResult:
        ...
