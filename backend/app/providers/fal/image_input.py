"""Validate scene stills and upload them to fal CDN (never private/local URLs)."""

from __future__ import annotations

import hashlib
import io
import ipaddress
import logging
import struct
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from PIL import Image

from app.providers.fal.client import FalAPIError, FalClient
from app.providers.fal.errors import FalClipError, FalErrorClass, FalErrorInfo

logger = logging.getLogger(__name__)

# Soft limits for i2v inputs.
_MAX_BYTES = 20 * 1024 * 1024  # 20 MB
_MIN_DIM = 32
_MAX_DIM = 8192
_TARGET_MAX_EDGE = 1280
_UPLOAD_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6h
_REDIS_KEY_PREFIX = "fal:upload:"

_upload_lock = threading.Lock()
_upload_cache: dict[str, str] = {}  # process fallback if Redis unavailable


@dataclass(slots=True, frozen=True)
class ValidatedImage:
    data: bytes
    content_type: str
    file_name: str
    width: int
    height: int
    sha256: str


def image_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clear_upload_cache() -> None:
    with _upload_lock:
        _upload_cache.clear()


def _redis_get(sha: str) -> str | None:
    try:
        from app.workers.redis_utils import get_redis_client

        val = get_redis_client().get(f"{_REDIS_KEY_PREFIX}{sha}")
        return str(val) if val else None
    except Exception as exc:
        logger.debug("redis upload cache get failed: %s", exc)
        return None


def _redis_set(sha: str, url: str) -> None:
    try:
        from app.workers.redis_utils import get_redis_client

        get_redis_client().setex(
            f"{_REDIS_KEY_PREFIX}{sha}",
            _UPLOAD_CACHE_TTL_SECONDS,
            url,
        )
    except Exception as exc:
        logger.debug("redis upload cache set failed: %s", exc)


def is_fal_cdn_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host.endswith(".fal.media") or host in {"fal.media", "v3.fal.media", "v3b.fal.media"}


def is_safe_public_image_url(url: str) -> bool:
    """True only for https URLs whose host is not localhost/private/link-local."""
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host in {"localhost", "127.0.0.1", "::1"}:
        return False
    if host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return False
    except ValueError:
        # Hostname (not an IP) — allowed if https.
        pass
    return bool(parsed.path)


def _sniff_format(data: bytes) -> tuple[str, str] | None:
    if len(data) < 24:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "scene.png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "scene.jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "scene.webp"
    return None


def _png_size(data: bytes) -> tuple[int, int]:
    # IHDR comes right after 8-byte signature.
    w, h = struct.unpack(">II", data[16:24])
    return int(w), int(h)


def _webp_size(data: bytes) -> tuple[int, int]:
    # VP8 / VP8L / VP8X
    if len(data) < 30:
        raise ValueError("webp too small")
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        # canvas width/height are 24-bit little-endian minus 1
        w = 1 + int.from_bytes(data[24:27], "little")
        h = 1 + int.from_bytes(data[27:30], "little")
        return w, h
    if chunk == b"VP8 " and len(data) >= 30:
        w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return int(w), int(h)
    if chunk == b"VP8L" and len(data) >= 25:
        b0, b1, b2, b3 = data[21:25]
        w = 1 + (((b1 & 0x3F) << 8) | b0)
        h = 1 + (((b3 & 0xF) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
        return int(w), int(h)
    raise ValueError("unsupported webp")


def _jpeg_size(data: bytes) -> tuple[int, int]:
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xD8:  # SOI
            i += 2
            continue
        if marker == 0xD9:  # EOI
            break
        if marker == 0x00:
            i += 2
            continue
        if i + 4 > n:
            break
        seglen = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if i + 9 >= n:
                break
            h, w = struct.unpack(">HH", data[i + 5 : i + 9])
            return int(w), int(h)
        if seglen < 2:
            break
        i += 2 + seglen
    raise ValueError("jpeg size not found")


def validate_image_bytes(data: bytes, *, claimed_mime: str | None = None) -> ValidatedImage:
    if not data:
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.FATAL,
                message="Image bytes are empty",
            )
        )
    if len(data) > _MAX_BYTES:
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.FATAL,
                message=f"Image exceeds {_MAX_BYTES} byte limit",
            )
        )
    sniffed = _sniff_format(data)
    if not sniffed:
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.FATAL,
                message="Unsupported image format (need jpg/png/webp)",
            )
        )
    content_type, file_name = sniffed
    if claimed_mime:
        claimed = claimed_mime.split(";")[0].strip().lower()
        if claimed and claimed not in {content_type, "image/jpg"}:
            # Tolerate image/jpg vs image/jpeg; otherwise keep sniffed type.
            if not (claimed == "image/jpg" and content_type == "image/jpeg"):
                pass
    try:
        if content_type == "image/png":
            width, height = _png_size(data)
        elif content_type == "image/jpeg":
            width, height = _jpeg_size(data)
        else:
            width, height = _webp_size(data)
    except Exception as exc:
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.FATAL,
                message=f"Could not read image dimensions: {exc}",
            )
        ) from exc

    if width < _MIN_DIM or height < _MIN_DIM or width > _MAX_DIM or height > _MAX_DIM:
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.FATAL,
                message=f"Image dimensions out of range ({width}x{height})",
            )
        )

    return ValidatedImage(
        data=data,
        content_type=content_type,
        file_name=file_name,
        width=width,
        height=height,
        sha256=image_sha256(data),
    )


def preprocess_image_bytes(
    data: bytes,
    *,
    max_edge: int = _TARGET_MAX_EDGE,
    quality: int = 88,
) -> ValidatedImage:
    """Resize (max edge) + re-encode as JPEG via Pillow for safer fal inputs."""
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        w, h = img.size
        edge = max(w, h)
        if edge > max_edge:
            scale = max_edge / float(edge)
            img = img.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
    except Exception as exc:
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.FATAL,
                message=f"Image preprocess failed: {exc}",
            )
        ) from exc
    return validate_image_bytes(out, claimed_mime="image/jpeg")


def resolve_fal_image_url(
    client: FalClient,
    *,
    image_bytes: bytes | None,
    image_mime: str | None,
    image_url: str | None,
) -> tuple[str, ValidatedImage | None]:
    """Return a fal-safe image_url. Prefer upload_bytes; never private/http URLs."""
    if image_bytes:
        validated = validate_image_bytes(image_bytes, claimed_mime=image_mime)
        return ensure_uploaded(client, validated), validated

    url = (image_url or "").strip()
    if not url:
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.FATAL,
                message="image_url or image_bytes required for image-to-video",
            )
        )
    if is_fal_cdn_url(url) or is_safe_public_image_url(url):
        return url, None

    raise FalClipError(
        FalErrorInfo(
            error_class=FalErrorClass.FATAL,
            message=(
                "Image URL is not publicly reachable by fal "
                "(must be https and non-private). Provide image_bytes instead."
            ),
            raw_body=url[:200],
        )
    )


def ensure_uploaded(client: FalClient, validated: ValidatedImage) -> str:
    cached = _redis_get(validated.sha256)
    if cached:
        return cached
    with _upload_lock:
        cached = _upload_cache.get(validated.sha256)
        if cached:
            return cached
    try:
        url = client.upload_bytes(
            validated.data,
            content_type=validated.content_type,
            file_name=validated.file_name,
        )
    except FalAPIError as exc:
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.RETRYABLE,
                message=str(exc),
                status_code=getattr(exc, "status_code", None),
                raw_body=str(exc),
            )
        ) from exc
    if not url or not str(url).startswith("http"):
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.RETRYABLE,
                message="fal upload returned empty URL",
            )
        )
    url_s = str(url)
    _redis_set(validated.sha256, url_s)
    with _upload_lock:
        _upload_cache[validated.sha256] = url_s
    return url_s


def cached_upload_url(sha256: str) -> str | None:
    hit = _redis_get(sha256)
    if hit:
        return hit
    with _upload_lock:
        return _upload_cache.get(sha256)
