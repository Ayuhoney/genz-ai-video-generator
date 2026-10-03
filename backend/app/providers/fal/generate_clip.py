"""Single entrypoint for fal image-to-video clips (queue + classify + budget)."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.providers.fal.adapters import get_adapter, parse_model_chain
from app.providers.fal.client import FalAPIError, FalClient, extract_video_url
from app.providers.fal.clip_controls import (
    bump_circuit_breaker,
    enforce_job_budget,
    ensure_project_not_paused,
    idempotency_bind,
    idempotency_claim,
    idempotency_clear,
    record_clip_cost_log,
)
from app.providers.fal.clip_timing import motion_only_prompt, ultra_safe_motion_prompt
from app.providers.fal.errors import (
    FalClipError,
    FalErrorClass,
    FalErrorInfo,
    client_facing_message,
    content_reject_target,
    extract_fal_error_fields,
)
from app.providers.fal.image_input import (
    ensure_uploaded,
    preprocess_image_bytes,
    resolve_fal_image_url,
    validate_image_bytes,
)
from app.providers.fal.prompt_sanitize import pre_sanitize_prompt

logger = logging.getLogger(__name__)

# Hard cap: retries + sanitize + fallbacks combined.
MAX_FAL_CALLS_PER_CLIP = 3


@dataclass(slots=True)
class ClipGenerateRequest:
    model_id: str
    prompt: str
    image_bytes: bytes | None = None
    image_mime: str | None = None
    image_url: str | None = None
    duration_seconds: float = 5.0
    resolution: str = "480p"
    disable_safety_checker: bool = False
    clip_id: str | None = None
    project_id: str | None = None
    model_chain: list[str] = field(default_factory=list)
    job_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    on_state: Callable[[str], None] | None = None


@dataclass(slots=True)
class ClipGenerateResult:
    video_url: str
    result: dict[str, Any]
    metrics: dict[str, Any]
    model_id: str
    prompt_used: str
    image_url: str
    request_id: str | None
    attempts: int
    prompt_sanitize_level: int
    estimated_cost_usd: float
    model_chain_used: list[str]


def _backoff(attempt: int, *, base: float = 0.5, cap: float = 8.0) -> float:
    delay = min(cap, base * (2**attempt))
    return delay * (0.5 + random.random())


def _settings() -> Any:
    try:
        from app.workers.settings import get_worker_settings

        return get_worker_settings()
    except Exception:
        from app.core.config import get_settings

        return get_settings()


def _emit(request: ClipGenerateRequest, state: str) -> None:
    if request.on_state:
        try:
            request.on_state(state)
        except Exception:
            pass


def _prompt_variants(raw_prompt: str) -> list[str]:
    cleaned = pre_sanitize_prompt(raw_prompt)
    soft = motion_only_prompt(cleaned)
    soft = pre_sanitize_prompt(soft)
    ultra = pre_sanitize_prompt(ultra_safe_motion_prompt(cleaned))
    out: list[str] = []
    for p in (soft, ultra):
        if p and p not in out:
            out.append(p)
    return out or [ultra]


def _to_clip_error(exc: BaseException) -> FalClipError:
    if isinstance(exc, FalClipError):
        return exc
    status = getattr(exc, "status_code", None)
    info = extract_fal_error_fields(str(exc), status_code=status)
    if isinstance(exc, FalAPIError) and getattr(exc, "error_info", None):
        info = exc.error_info  # type: ignore[attr-defined]
    return FalClipError(info)


def _dry_run_clip(request: ClipGenerateRequest, settings: Any) -> ClipGenerateResult:
    """Full generate_clip path without any fal HTTP — prints estimated costs."""
    chain = request.model_chain or parse_model_chain(
        getattr(settings, "fal_video_model_chain", None),
        primary=request.model_id or getattr(settings, "fal_video_model", None),
    )
    model_id = chain[0] if chain else request.model_id
    adapter = get_adapter(model_id)
    estimate = (
        adapter.estimate_cost_usd(
            duration_seconds=request.duration_seconds, settings=settings
        )
        if adapter
        else float(getattr(settings, "fal_video_cost_usd", 0.05) or 0.05)
    )
    job_est = estimate
    if request.project_id:
        try:
            from app.providers.fal.clip_controls import estimated_job_spend
            from app.storage.asset_store import get_sync_db_from_settings

            job_est = estimated_job_spend(
                get_sync_db_from_settings(), request.project_id
            ) + estimate
        except Exception:
            job_est = estimate
    prompt = _prompt_variants(request.prompt)[0]
    msg = (
        f"[DRY_RUN] clip_id={request.clip_id} model={model_id} "
        f"estimated_clip_usd={estimate:.4f} estimated_job_total_usd={job_est:.4f} "
        f"prompt={prompt[:80]!r}"
    )
    print(msg, flush=True)
    logger.info(msg)
    _emit(request, "completed")
    return ClipGenerateResult(
        video_url="dry-run://clip.mp4",
        result={
            "video": {"url": "dry-run://clip.mp4", "content_type": "video/mp4"},
            "dry_run": True,
        },
        metrics={"inference_time": 0.0, "request_id": "dry-run", "dry_run": True},
        model_id=model_id,
        prompt_used=prompt,
        image_url="dry-run://image",
        request_id="dry-run",
        attempts=0,
        prompt_sanitize_level=0,
        estimated_cost_usd=estimate,
        model_chain_used=list(chain[:1]),
    )


def generate_clip(
    client: FalClient,
    request: ClipGenerateRequest,
    *,
    max_fal_calls: int = MAX_FAL_CALLS_PER_CLIP,
    sleep_fn: Callable[[float], None] | None = None,
    run_fn: Callable[[str, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> ClipGenerateResult:
    """Submit→poll with max N fal calls, model fallbacks, Redis idempotency."""
    sleep = sleep_fn or time.sleep
    run = run_fn or client.run
    settings = _settings()

    if request.project_id:
        ensure_project_not_paused(request.project_id)

    if bool(getattr(settings, "dry_run", False)):
        return _dry_run_clip(request, settings)

    # Idempotency: never double-submit the same clip_id.
    if request.clip_id:
        prior = idempotency_claim(request.clip_id)
        if prior == "pending":
            raise FalClipError(
                FalErrorInfo(
                    error_class=FalErrorClass.RETRYABLE,
                    message=client_facing_message(
                        FalErrorClass.RETRYABLE, exhausted=False
                    ),
                    request_id=prior,
                ),
                clip_status="failed_retryable",
            )
        if prior:
            logger.warning(
                "fal generate_clip blocked duplicate submit clip_id=%s prior=%s",
                request.clip_id,
                prior,
            )
            raise FalClipError(
                FalErrorInfo(
                    error_class=FalErrorClass.FATAL,
                    message=client_facing_message(FalErrorClass.FATAL),
                    request_id=prior,
                ),
                clip_status="failed",
            )

    _emit(request, "uploading")
    image_bytes = request.image_bytes
    image_mime = request.image_mime
    validated = None
    if image_bytes:
        validated = validate_image_bytes(image_bytes, claimed_mime=image_mime)
        image_bytes = validated.data
        image_mime = validated.content_type

    image_url, validated = resolve_fal_image_url(
        client,
        image_bytes=image_bytes,
        image_mime=image_mime,
        image_url=request.image_url,
    )

    chain = request.model_chain or parse_model_chain(
        getattr(settings, "fal_video_model_chain", None),
        primary=request.model_id or getattr(settings, "fal_video_model", None),
    )
    prompts = _prompt_variants(request.prompt)

    fal_calls = 0
    prompt_level = 0
    model_index = 0
    preprocess_done = False
    last_reject: FalClipError | None = None
    retryable_streak = 0
    models_tried: list[str] = []

    while fal_calls < max_fal_calls and model_index < len(chain):
        model_id = chain[model_index]
        adapter = get_adapter(model_id)
        if adapter is None:
            model_index += 1
            continue
        if model_id not in models_tried:
            models_tried.append(model_id)

        prompt = prompts[min(prompt_level, len(prompts) - 1)]
        estimate = adapter.estimate_cost_usd(
            duration_seconds=request.duration_seconds,
            settings=settings,
        )
        if request.project_id:
            enforce_job_budget(request.project_id, next_estimate=estimate)

        arguments = adapter.build_arguments(
            prompt=prompt,
            image_url=image_url,
            duration_seconds=request.duration_seconds,
            resolution=request.resolution,
            disable_safety_checker=request.disable_safety_checker,
            extra=request.extra,
        )

        _emit(request, "queued")
        fal_calls += 1
        _emit(request, "generating")
        try:
            result, metrics = run(model_id, arguments)
            video_url = extract_video_url(result)
            request_id = None
            if isinstance(metrics, dict):
                request_id = metrics.get("request_id")
            if request.clip_id:
                idempotency_bind(request.clip_id, str(request_id or f"ok:{fal_calls}"))
            if request.project_id:
                bump_circuit_breaker(request.project_id, failed=False)
                record_clip_cost_log(
                    project_id=request.project_id,
                    clip_id=request.clip_id,
                    model_id=model_id,
                    estimated=estimate,
                    fal_calls=fal_calls,
                )
            _emit(request, "completed")
            logger.info(
                "fal generate_clip ok clip_id=%s model=%s fal_calls=%d "
                "prompt_level=%d request_id=%s estimated_usd=%.4f",
                request.clip_id,
                model_id,
                fal_calls,
                prompt_level,
                request_id,
                estimate,
            )
            return ClipGenerateResult(
                video_url=video_url,
                result=result,
                metrics=metrics,
                model_id=model_id,
                prompt_used=prompt,
                image_url=image_url,
                request_id=str(request_id) if request_id else None,
                attempts=fal_calls,
                prompt_sanitize_level=prompt_level,
                estimated_cost_usd=estimate,
                model_chain_used=list(models_tried),
            )
        except Exception as exc:  # noqa: BLE001
            err = _to_clip_error(exc)
            logger.error(
                "fal generate_clip fail clip_id=%s model=%s fal_calls=%d "
                "class=%s detail_type=%s loc=%s request_id=%s msg=%s",
                request.clip_id,
                model_id,
                fal_calls,
                err.error_class.value,
                err.info.detail_type,
                err.info.detail_loc,
                err.info.request_id,
                err.info.message,
            )

            if err.error_class == FalErrorClass.CONTENT_REJECTED:
                last_reject = err
                retryable_streak = 0
                # Escalate: soft→ultra, then preprocess once, then next model.
                if prompt_level + 1 < len(prompts):
                    prompt_level += 1
                    _emit(request, "retrying")
                    continue
                if not preprocess_done and image_bytes:
                    preprocess_done = True
                    validated = preprocess_image_bytes(image_bytes)
                    image_bytes = validated.data
                    image_mime = validated.content_type
                    image_url = ensure_uploaded(client, validated)
                    prompt_level = len(prompts) - 1
                    _emit(request, "retrying")
                    continue
                # Fallback model with ultra prompt if budget remains.
                if model_index + 1 < len(chain):
                    model_index += 1
                    prompt_level = len(prompts) - 1
                    _emit(request, "retrying")
                    continue
                break

            if err.error_class == FalErrorClass.FATAL:
                if request.project_id:
                    bump_circuit_breaker(request.project_id, failed=True)
                if request.clip_id:
                    idempotency_clear(request.clip_id)
                raise FalClipError(
                    FalErrorInfo(
                        error_class=FalErrorClass.FATAL,
                        message=client_facing_message(FalErrorClass.FATAL),
                        status_code=err.info.status_code,
                        detail_type=err.info.detail_type,
                        detail_loc=list(err.info.detail_loc),
                        request_id=err.info.request_id,
                        raw_body=err.info.raw_body,
                    ),
                    clip_status="failed",
                    fal_calls=fal_calls,
                ) from exc

            # RETRYABLE — same model if calls remain; else next model.
            retryable_streak += 1
            _emit(request, "retrying")
            if fal_calls >= max_fal_calls:
                break
            if retryable_streak >= 2 and model_index + 1 < len(chain):
                model_index += 1
                retryable_streak = 0
                continue
            sleep(_backoff(fal_calls - 1))

    if request.project_id:
        bump_circuit_breaker(request.project_id, failed=True)
    if request.clip_id:
        idempotency_clear(request.clip_id)

    if last_reject is not None:
        target = content_reject_target(last_reject.info)
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.CONTENT_REJECTED,
                message=client_facing_message(FalErrorClass.CONTENT_REJECTED),
                status_code=last_reject.info.status_code,
                detail_type=last_reject.info.detail_type,
                detail_loc=list(last_reject.info.detail_loc),
                request_id=last_reject.info.request_id,
                raw_body=last_reject.info.raw_body,
            ),
            clip_status=target,
            fal_calls=fal_calls,
        )

    raise FalClipError(
        FalErrorInfo(
            error_class=FalErrorClass.RETRYABLE,
            message=client_facing_message(FalErrorClass.RETRYABLE, exhausted=True),
        ),
        clip_status="failed_retryable",
        fal_calls=fal_calls,
    )


# Back-compat helpers used by older tests
def build_wan_turbo_arguments(**kwargs: Any) -> dict[str, Any]:
    from app.providers.fal.adapters import WanTurboAdapter

    return WanTurboAdapter().build_arguments(
        prompt=kwargs.get("prompt", ""),
        image_url=kwargs.get("image_url", ""),
        duration_seconds=float(kwargs.get("duration_seconds") or 5),
        resolution=str(kwargs.get("resolution") or "480p"),
        disable_safety_checker=bool(kwargs.get("disable_safety_checker")),
        extra=kwargs.get("extra"),
    )


def build_model_arguments(model_id: str, **kwargs: Any) -> dict[str, Any]:
    adapter = get_adapter(model_id)
    if adapter is None:
        raise FalClipError(
            FalErrorInfo(
                error_class=FalErrorClass.FATAL,
                message=f"Unsupported fal video model: {model_id}",
            )
        )
    return adapter.build_arguments(
        prompt=kwargs.get("prompt", ""),
        image_url=kwargs.get("image_url", ""),
        duration_seconds=float(kwargs.get("duration_seconds") or 5),
        resolution=str(kwargs.get("resolution") or "480p"),
        disable_safety_checker=bool(kwargs.get("disable_safety_checker")),
        extra=kwargs.get("extra"),
    )


__all__ = [
    "ClipGenerateRequest",
    "ClipGenerateResult",
    "MAX_FAL_CALLS_PER_CLIP",
    "build_model_arguments",
    "build_wan_turbo_arguments",
    "generate_clip",
]
