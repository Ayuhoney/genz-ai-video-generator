"""Redis broker helpers supporting redis:// and rediss:// (TLS)."""

from __future__ import annotations

import ssl
from typing import Any, Literal

import redis

_redis_client: redis.Redis | None = None


def redis_ssl_cert_reqs(
    value: Literal["none", "optional", "required"],
) -> ssl.VerifyMode | None:
    mapping = {
        "none": ssl.CERT_NONE,
        "optional": ssl.CERT_OPTIONAL,
        "required": ssl.CERT_REQUIRED,
    }
    return mapping[value]


def celery_broker_url(redis_url: str) -> str:
    return redis_url


def celery_broker_use_ssl(
    redis_url: str,
    *,
    cert_reqs: Literal["none", "optional", "required"] = "required",
) -> dict[str, Any] | None:
    if not redis_url.startswith("rediss://"):
        return None
    return {"ssl_cert_reqs": redis_ssl_cert_reqs(cert_reqs)}


def get_redis_client(redis_url: str | None = None) -> redis.Redis:
    """Process-wide Redis client for caches / idempotency (not Celery broker opts)."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    url = redis_url
    if not url:
        try:
            from app.workers.settings import get_worker_settings

            url = get_worker_settings().redis_url
        except Exception:
            url = "redis://localhost:6379/0"
    kwargs: dict[str, Any] = {"decode_responses": True}
    if url.startswith("rediss://"):
        try:
            from app.workers.settings import get_worker_settings

            cert = get_worker_settings().redis_ssl_cert_reqs
        except Exception:
            cert = "none"
        kwargs["ssl_cert_reqs"] = redis_ssl_cert_reqs(cert)  # type: ignore[arg-type]
    _redis_client = redis.Redis.from_url(url, **kwargs)
    return _redis_client


def reset_redis_client() -> None:
    global _redis_client
    if _redis_client is not None:
        try:
            _redis_client.close()
        except Exception:
            pass
    _redis_client = None
