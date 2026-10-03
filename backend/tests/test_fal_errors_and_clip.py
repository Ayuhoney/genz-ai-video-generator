"""Unit tests for fal error classification + generate_clip retry (mocked)."""

from __future__ import annotations

import struct
import zlib

import pytest

from app.providers.fal.adapters import (
    WAN_FULL,
    WAN_TURBO,
    WanFullAdapter,
    WanTurboAdapter,
    parse_model_chain,
)
from app.providers.fal.errors import (
    FalErrorClass,
    classify_fal_error,
    content_reject_target,
    extract_fal_error_fields,
)
from app.providers.fal.generate_clip import (
    MAX_FAL_CALLS_PER_CLIP,
    ClipGenerateRequest,
    generate_clip,
)
from app.providers.fal.image_input import (
    clear_upload_cache,
    is_safe_public_image_url,
    preprocess_image_bytes,
    validate_image_bytes,
)
from app.providers.fal.prompt_sanitize import pre_sanitize_prompt


def _minimal_png(width: int = 64, height: int = 64) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\x00" * (width * 3) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


CONTENT_422 = (
    'fal result failed (422): {"detail":[{"loc":["body","prompt"],'
    '"msg":"The content could not be processed because it contained material '
    'flagged by a content checker.","type":"content_policy_violation",'
    '"url":"https://docs.fal.ai/errors#content_policy_violation"}]}'
)

DOWNLOAD_422 = (
    'fal result failed (422): {"detail":[{"loc":["body","image_url"],'
    '"msg":"Failed to download the file. Please check if the URL is accessible '
    'and try again.","type":"file_download_error"}]}'
)


def test_classify_content_policy_from_detail_type() -> None:
    info = extract_fal_error_fields(CONTENT_422, status_code=422)
    assert info.error_class == FalErrorClass.CONTENT_REJECTED
    assert info.detail_type == "content_policy_violation"
    assert info.detail_loc == ["body", "prompt"]
    assert content_reject_target(info) == "needs_new_prompt"


def test_classify_download_error_retryable() -> None:
    info = extract_fal_error_fields(DOWNLOAD_422, status_code=422)
    assert info.error_class == FalErrorClass.RETRYABLE


def test_classify_5xx_and_429() -> None:
    assert classify_fal_error("server exploded", status_code=503) == FalErrorClass.RETRYABLE
    assert classify_fal_error("slow down", status_code=429) == FalErrorClass.RETRYABLE


def test_classify_auth_fatal() -> None:
    assert (
        classify_fal_error("Unauthorized: invalid key", status_code=401)
        == FalErrorClass.FATAL
    )


def test_reject_private_http_urls() -> None:
    assert not is_safe_public_image_url("http://200.234.41.50:9100/x.jpg")
    assert not is_safe_public_image_url("https://10.0.0.5/x.jpg")
    assert is_safe_public_image_url(
        "https://storage.googleapis.com/falserverless/model_tests/wan/dragon-warrior.jpg"
    )


def test_validate_and_preprocess_png() -> None:
    png = _minimal_png(200, 100)
    v = validate_image_bytes(png)
    assert v.width == 200
    pre = preprocess_image_bytes(png, max_edge=64)
    assert pre.content_type == "image/jpeg"
    assert max(pre.width, pre.height) <= 64


def test_pre_sanitize_strips_blocked_words() -> None:
    out = pre_sanitize_prompt("epic blood and gore battle")
    assert "blood" not in out.lower()
    assert "gore" not in out.lower()


def test_adapters_build_distinct_args() -> None:
    turbo = WanTurboAdapter().build_arguments(
        prompt="p",
        image_url="https://cdn.example/a.jpg",
        duration_seconds=10,
        resolution="480p",
        disable_safety_checker=False,
    )
    assert "num_frames" not in turbo
    assert turbo["resolution"] == "480p"
    full = WanFullAdapter().build_arguments(
        prompt="p",
        image_url="https://cdn.example/a.jpg",
        duration_seconds=10,
        resolution="480p",
        disable_safety_checker=False,
    )
    assert "num_frames" in full
    assert "guidance_scale" in full


def test_parse_model_chain_env() -> None:
    chain = parse_model_chain(f"{WAN_TURBO},{WAN_FULL},fal-ai/unknown/model")
    assert chain == [WAN_TURBO, WAN_FULL]


def test_generate_clip_caps_fal_calls_at_three(monkeypatch) -> None:
    clear_upload_cache()
    png = _minimal_png()
    calls: list[str] = []

    class FakeClient:
        def upload_bytes(self, data, *, content_type, file_name="x"):
            return "https://v3b.fal.media/files/test/scene.png"

    def run_fn(model_id, arguments):
        calls.append(model_id)
        raise RuntimeError(DOWNLOAD_422)

    monkeypatch.setattr(
        "app.providers.fal.generate_clip.idempotency_claim",
        lambda _cid: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.idempotency_clear",
        lambda _cid: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.enforce_job_budget",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.ensure_project_not_paused",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.bump_circuit_breaker",
        lambda *_a, **_k: None,
    )

    with pytest.raises(Exception) as ei:
        generate_clip(
            FakeClient(),  # type: ignore[arg-type]
            ClipGenerateRequest(
                model_id=WAN_TURBO,
                prompt="calm throne room",
                image_bytes=png,
                model_chain=[WAN_TURBO, WAN_FULL],
                clip_id="c-cap",
            ),
            max_fal_calls=MAX_FAL_CALLS_PER_CLIP,
            sleep_fn=lambda _d: None,
            run_fn=run_fn,
        )
    assert getattr(ei.value, "clip_status", None) == "failed_retryable"
    assert len(calls) == MAX_FAL_CALLS_PER_CLIP


def test_generate_clip_content_reject_sanitizes_within_budget(monkeypatch) -> None:
    clear_upload_cache()
    png = _minimal_png()
    prompts: list[str] = []

    class FakeClient:
        def upload_bytes(self, data, *, content_type, file_name="x"):
            return "https://v3b.fal.media/files/test/scene.png"

    def run_fn(model_id, arguments):
        prompts.append(arguments["prompt"])
        raise RuntimeError(CONTENT_422)

    monkeypatch.setattr(
        "app.providers.fal.generate_clip.idempotency_claim",
        lambda _cid: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.idempotency_clear",
        lambda _cid: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.enforce_job_budget",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.ensure_project_not_paused",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.providers.fal.generate_clip.bump_circuit_breaker",
        lambda *_a, **_k: None,
    )

    with pytest.raises(Exception) as ei:
        generate_clip(
            FakeClient(),  # type: ignore[arg-type]
            ClipGenerateRequest(
                model_id=WAN_TURBO,
                prompt="Wide shot fight scene with blood",
                image_bytes=png,
                model_chain=[WAN_TURBO],
            ),
            max_fal_calls=3,
            sleep_fn=lambda _d: None,
            run_fn=run_fn,
        )
    assert getattr(ei.value, "error_class", None) == FalErrorClass.CONTENT_REJECTED
    assert getattr(ei.value, "clip_status", None) in {
        "needs_new_prompt",
        "needs_new_image",
    }
    # soft + ultra (+ optional preprocess upload uses another call)
    assert 2 <= len(prompts) <= 3
    assert "blood" not in prompts[0].lower()
