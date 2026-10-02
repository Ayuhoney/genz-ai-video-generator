"""Celery application — worker process entrypoint (no FastAPI imports)."""

from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init

from app.media.ffmpeg_tools import require_ffmpeg_tools
from app.workers.redis_utils import celery_broker_url, celery_broker_use_ssl
from app.workers.settings import get_worker_settings


@worker_process_init.connect
def _require_ffmpeg_on_worker_start(**_kwargs: object) -> None:
    """Fail fast if ffmpeg/ffprobe are missing on worker hosts."""
    require_ffmpeg_tools()

settings = get_worker_settings()

celery_app = Celery(
    "ai_video_platform",
    broker=celery_broker_url(settings.redis_url),
    backend=celery_broker_url(settings.redis_url),
    include=[
        "app.workers.tasks.image",
        "app.workers.tasks.video",
        "app.workers.tasks.tts",
        "app.workers.tasks.sfx",
        "app.workers.tasks.music",
        "app.workers.tasks.scene_render",
        "app.workers.tasks.final_assembly",
    ],
)

broker_ssl = celery_broker_use_ssl(
    settings.redis_url,
    cert_reqs=settings.redis_ssl_cert_reqs,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_retry_delay=1,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_soft_time_limit=settings.celery_soft_time_limit,
    task_time_limit=settings.celery_time_limit,
    broker_use_ssl=broker_ssl,
    redis_backend_use_ssl=broker_ssl,
)

celery_app.conf.task_routes = {
    "workers.image.*": {"queue": "media"},
    "workers.video.*": {"queue": "media"},
    "workers.tts.*": {"queue": "audio"},
    "workers.sfx.*": {"queue": "audio"},
    "workers.music.*": {"queue": "audio"},
    "workers.ffmpeg.*": {"queue": "ffmpeg"},
    "workers.scene_render.*": {"queue": "media"},
    "workers.final_assembly.*": {"queue": "ffmpeg"},
}
