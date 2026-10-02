"""Redis broker helpers supporting redis:// and rediss:// (TLS)."""

from __future__ import annotations

import ssl
from typing import Any, Literal


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
