"""Classify fal.ai failures from real response bodies (not string guesses alone)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FalErrorClass(str, Enum):
    RETRYABLE = "RETRYABLE"
    CONTENT_REJECTED = "CONTENT_REJECTED"
    FATAL = "FATAL"


@dataclass(slots=True)
class FalErrorInfo:
    error_class: FalErrorClass
    message: str
    status_code: int | None = None
    detail_type: str | None = None
    detail_loc: list[Any] = field(default_factory=list)
    request_id: str | None = None
    raw_body: str | None = None


class FalClipError(Exception):
    """Typed fal clip failure for generate_clip / workers."""

    def __init__(
        self,
        info: FalErrorInfo,
        *,
        clip_status: str | None = None,
        fal_calls: int | None = None,
    ) -> None:
        super().__init__(info.message)
        self.info = info
        self.clip_status = clip_status  # e.g. needs_new_prompt / needs_new_image
        self.error_class = info.error_class
        self.fal_request_id = info.request_id
        self.fal_calls = fal_calls
        # Terminal clip statuses must not be retried by registry/celery outer loops.
        self.retryable = (
            info.error_class == FalErrorClass.RETRYABLE and clip_status is None
        )


_DOWNLOAD_MARKERS = (
    "failed to download the file",
    "check if the url is accessible",
    "could not download",
    "unable to download",
    "error while downloading",
)

_AUTH_MARKERS = (
    "unauthorized",
    "forbidden",
    "invalid key",
    "authentication",
    "not authenticated",
)

_BILLING_MARKERS = (
    "insufficient",
    "balance",
    "credits",
    "payment",
    "billing",
    "quota exceeded",
)


def _parse_json_body(text: str | None) -> dict[str, Any] | list[Any] | None:
    if not text:
        return None
    raw = text.strip()
    # Client often prefixes: "fal result failed (422): {...}"
    brace = raw.find("{")
    bracket = raw.find("[")
    start = -1
    if brace >= 0 and (bracket < 0 or brace < bracket):
        start = brace
    elif bracket >= 0:
        start = bracket
    if start < 0:
        return None
    try:
        return json.loads(raw[start:])
    except json.JSONDecodeError:
        return None


def _first_detail(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, list) and detail:
            first = detail[0]
            return first if isinstance(first, dict) else None
        if isinstance(detail, dict):
            return detail
        if isinstance(payload.get("type"), str):
            return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return None


def extract_fal_error_fields(
    body: str | None,
    *,
    status_code: int | None = None,
) -> FalErrorInfo:
    """Parse fal error JSON into structured fields."""
    payload = _parse_json_body(body)
    detail = _first_detail(payload) if payload is not None else None
    detail_type = None
    detail_loc: list[Any] = []
    message = (body or "fal error").strip()
    request_id = None

    if isinstance(payload, dict):
        request_id = payload.get("request_id") or payload.get("requestId")
        detail_obj = payload.get("detail")
        if request_id is None and isinstance(detail_obj, dict):
            request_id = detail_obj.get("request_id") or detail_obj.get("requestId")
    if detail:
        detail_type = str(detail.get("type") or "") or None
        loc = detail.get("loc")
        if isinstance(loc, list):
            detail_loc = list(loc)
        if detail.get("msg"):
            message = str(detail["msg"])
        elif detail.get("message"):
            message = str(detail["message"])

    info = FalErrorInfo(
        error_class=FalErrorClass.FATAL,  # filled by classify
        message=message,
        status_code=status_code,
        detail_type=detail_type,
        detail_loc=detail_loc,
        request_id=str(request_id) if request_id else None,
        raw_body=(body or "")[:2000] or None,
    )
    info.error_class = classify_fal_error(info)
    return info


def classify_fal_error(info: FalErrorInfo | str, *, status_code: int | None = None) -> FalErrorClass:
    """Map fal failure → RETRYABLE | CONTENT_REJECTED | FATAL."""
    if isinstance(info, str):
        info = extract_fal_error_fields(info, status_code=status_code)

    code = info.status_code if info.status_code is not None else status_code
    text = (info.message or "").lower()
    raw = (info.raw_body or "").lower()
    blob = f"{text}\n{raw}"
    dtype = (info.detail_type or "").lower()

    # Primary signal from fal schema/errors docs.
    if dtype == "content_policy_violation" or "content_policy_violation" in blob:
        return FalErrorClass.CONTENT_REJECTED
    if any(
        m in blob
        for m in (
            "content checker",
            "flagged by a content checker",
            "safety checker",
            "content could not be processed",
        )
    ):
        return FalErrorClass.CONTENT_REJECTED

    if code in {401, 403} or any(m in blob for m in _AUTH_MARKERS):
        return FalErrorClass.FATAL
    if any(m in blob for m in _BILLING_MARKERS):
        return FalErrorClass.FATAL

    # Download / URL fetch failures are retryable (often private URL / transient CDN).
    if any(m in blob for m in _DOWNLOAD_MARKERS):
        return FalErrorClass.RETRYABLE

    if code == 429 or code == 408:
        return FalErrorClass.RETRYABLE
    if code is not None and code >= 500:
        return FalErrorClass.RETRYABLE
    if code == 202:
        return FalErrorClass.RETRYABLE

    if "timed out" in blob or "timeout" in blob or "network" in blob:
        return FalErrorClass.RETRYABLE
    if "connection" in blob or "temporarily unavailable" in blob:
        return FalErrorClass.RETRYABLE

    # Validation / bad input (non-download 422 without content policy).
    if code == 422:
        return FalErrorClass.FATAL
    if code is not None and 400 <= code < 500:
        return FalErrorClass.FATAL

    return FalErrorClass.RETRYABLE


def is_content_blocked_error(exc: BaseException) -> bool:
    if isinstance(exc, FalClipError):
        return exc.error_class == FalErrorClass.CONTENT_REJECTED
    info = extract_fal_error_fields(str(exc), status_code=getattr(exc, "status_code", None))
    return info.error_class == FalErrorClass.CONTENT_REJECTED


def content_reject_target(info: FalErrorInfo) -> str:
    """Return needs_new_prompt | needs_new_image from detail.loc."""
    loc = [str(x).lower() for x in (info.detail_loc or [])]
    joined = "/".join(loc)
    if "image" in joined or "image_url" in joined:
        return "needs_new_image"
    if "prompt" in joined:
        return "needs_new_prompt"
    # Default: our captures were prompt-driven.
    return "needs_new_prompt"


def client_facing_message(error_class: FalErrorClass, *, exhausted: bool = False) -> str:
    if error_class == FalErrorClass.CONTENT_REJECTED:
        return (
            "This scene couldn't be processed as-is. "
            "Please adjust the prompt or upload a different image."
        )
    if error_class == FalErrorClass.RETRYABLE:
        if exhausted:
            return "Generation is taking longer than expected. We're retrying automatically."
        return "Generation is taking longer than expected. We're retrying automatically."
    return "Something went wrong. Our team has been notified."


_REQUEST_ID_RE = re.compile(r"request_id[=:]?\s*['\"]?([a-zA-Z0-9_-]+)")


def request_id_from_text(text: str) -> str | None:
    m = _REQUEST_ID_RE.search(text or "")
    return m.group(1) if m else None
