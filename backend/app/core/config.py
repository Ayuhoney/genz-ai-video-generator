from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mongodb_url: str = Field(alias="MONGODB_URL")
    mongodb_db_name: str = Field(
        default="ai_video_platform",
        alias="MONGODB_DB_NAME",
    )
    jwt_secret: str = Field(alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=60, alias="JWT_EXPIRE_MINUTES")
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"],
        alias="CORS_ORIGINS",
    )

    # Shared with workers (API may use rediss:// remote Redis)
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_ssl_cert_reqs: Literal["none", "optional", "required"] = Field(
        default="required",
        alias="REDIS_SSL_CERT_REQS",
    )
    internal_api_token: str = Field(
        default="dev-internal-token-change-me",
        alias="INTERNAL_API_TOKEN",
    )
    api_base_url: str = Field(
        default="http://127.0.0.1:8000",
        alias="API_BASE_URL",
    )
    celery_task_always_eager: bool = Field(
        default=False,
        alias="CELERY_TASK_ALWAYS_EAGER",
    )
    celery_max_retries: int = Field(default=3, alias="CELERY_MAX_RETRIES")
    celery_soft_time_limit: int = Field(default=30, alias="CELERY_SOFT_TIME_LIMIT")
    celery_time_limit: int = Field(default=60, alias="CELERY_TIME_LIMIT")
    mock_task_sleep_seconds: float = Field(
        default=0.05,
        alias="MOCK_TASK_SLEEP_SECONDS",
    )
    allow_mock: bool = Field(default=False, alias="ALLOW_MOCK")

    # Cloudflare R2 (optional — falls back to local ./media)
    r2_account_id: str | None = Field(default=None, alias="R2_ACCOUNT_ID")
    r2_access_key_id: str | None = Field(default=None, alias="R2_ACCESS_KEY_ID")
    r2_secret_access_key: str | None = Field(default=None, alias="R2_SECRET_ACCESS_KEY")
    r2_bucket: str | None = Field(default=None, alias="R2_BUCKET")
    r2_public_base_url: str | None = Field(default=None, alias="R2_PUBLIC_BASE_URL")
    r2_endpoint_url: str | None = Field(default=None, alias="R2_ENDPOINT_URL")
    media_local_root: str = Field(default="./media", alias="MEDIA_LOCAL_ROOT")
    presigned_url_expire_seconds: int = Field(
        default=3600,
        alias="PRESIGNED_URL_EXPIRE_SECONDS",
    )

    # Providers — production defaults (tests override via env)
    provider_image: str = Field(default="fal", alias="PROVIDER_IMAGE")
    provider_video: str = Field(default="fal", alias="PROVIDER_VIDEO")
    provider_tts: str = Field(default="sarvam", alias="PROVIDER_TTS")
    provider_sfx: str = Field(default="fal", alias="PROVIDER_SFX")
    provider_music: str = Field(default="fal", alias="PROVIDER_MUSIC")
    provider_image_fallbacks: str = Field(default="", alias="PROVIDER_IMAGE_FALLBACKS")
    provider_video_fallbacks: str = Field(default="", alias="PROVIDER_VIDEO_FALLBACKS")
    provider_tts_fallbacks: str = Field(default="", alias="PROVIDER_TTS_FALLBACKS")
    provider_sfx_fallbacks: str = Field(default="", alias="PROVIDER_SFX_FALLBACKS")
    provider_music_fallbacks: str = Field(default="", alias="PROVIDER_MUSIC_FALLBACKS")
    provider_timeout_seconds: float = Field(
        default=30.0,
        alias="PROVIDER_TIMEOUT_SECONDS",
    )
    provider_max_retries: int = Field(default=2, alias="PROVIDER_MAX_RETRIES")

    # fal.ai — model IDs must be set via env (never hardcoded defaults)
    fal_key: str | None = Field(default=None, alias="FAL_KEY")
    fal_image_model: str | None = Field(default=None, alias="FAL_IMAGE_MODEL")
    fal_image_i2i_model: str = Field(
        default="fal-ai/flux/dev/image-to-image",
        alias="FAL_IMAGE_I2I_MODEL",
    )
    fal_image_i2i_strength: float = Field(
        default=0.65,
        alias="FAL_IMAGE_I2I_STRENGTH",
    )
    fal_look_lock_wait_seconds: float = Field(
        default=600.0,
        alias="FAL_LOOK_LOCK_WAIT_SECONDS",
    )
    fal_video_model: str | None = Field(default=None, alias="FAL_VIDEO_MODEL")
    fal_webhook_url: str | None = Field(default=None, alias="FAL_WEBHOOK_URL")
    fal_poll_interval_seconds: float = Field(
        default=2.0,
        alias="FAL_POLL_INTERVAL_SECONDS",
    )
    fal_poll_timeout_seconds: float = Field(
        default=600.0,
        alias="FAL_POLL_TIMEOUT_SECONDS",
    )
    fal_image_cost_usd: float = Field(default=0.01, alias="FAL_IMAGE_COST_USD")
    fal_image_cost_per_second: float = Field(
        default=0.0,
        alias="FAL_IMAGE_COST_PER_SECOND",
    )
    fal_video_cost_usd: float = Field(default=0.05, alias="FAL_VIDEO_COST_USD")
    fal_video_cost_per_second: float = Field(
        default=0.05,
        alias="FAL_VIDEO_COST_PER_SECOND",
    )
    max_project_cost_usd: float = Field(default=10.0, alias="MAX_PROJECT_COST_USD")

    # Film studio audio uses Sarvam TTS + fal SFX/music (see app.film_studio).
    # Extra fal model IDs for studio SFX / music:
    fal_sfx_model: str | None = Field(default=None, alias="FAL_SFX_MODEL")
    fal_music_model: str | None = Field(default=None, alias="FAL_MUSIC_MODEL")
    fal_video_resolution: str = Field(default="480p", alias="FAL_VIDEO_RESOLUTION")
    fal_video_clip_seconds: int = Field(default=5, alias="FAL_VIDEO_CLIP_SECONDS")
    shot_duration_seconds: int = Field(default=5, alias="SHOT_DURATION_SECONDS")
    fal_disable_safety_checker: bool = Field(
        default=False,
        alias="FAL_DISABLE_SAFETY_CHECKER",
    )
    fal_video_model_chain: str = Field(
        default=(
            "fal-ai/wan/v2.2-a14b/image-to-video/turbo,"
            "fal-ai/wan/v2.2-a14b/image-to-video"
        ),
        alias="FAL_VIDEO_MODEL_CHAIN",
    )
    job_max_usd: float = Field(default=5.0, alias="JOB_MAX_USD")
    dry_run: bool = Field(default=False, alias="DRY_RUN")
    fal_prompt_blocked_words: str = Field(
        default="",
        alias="FAL_PROMPT_BLOCKED_WORDS",
    )
    fal_sfx_cost_usd: float = Field(default=0.006, alias="FAL_SFX_COST_USD")
    fal_music_cost_usd: float = Field(default=0.01, alias="FAL_MUSIC_COST_USD")
    sarvam_key: str | None = Field(default=None, alias="SARVAM_KEY")
    sarvam_tts_model: str = Field(default="bulbul:v3", alias="SARVAM_TTS_MODEL")
    sarvam_timeout_seconds: float = Field(default=60.0, alias="SARVAM_TIMEOUT_SECONDS")
    sarvam_tts_cost_usd: float = Field(default=0.002, alias="SARVAM_TTS_COST_USD")
    # Director uses Groq (OpenAI-compatible). Gemini retained only for film_studio if set.
    groq_key: str | None = Field(default=None, alias="GROQ_KEY")
    groq_model: str = Field(default="openai/gpt-oss-120b", alias="GROQ_MODEL")
    gemini_key: str | None = Field(default=None, alias="GEMINI_KEY")
    gemini_model: str = Field(default="gemini-3.8-flash", alias="GEMINI_MODEL")
    director_provider: str = Field(default="groq", alias="DIRECTOR_PROVIDER")

    ffmpeg_width: int = Field(default=1280, alias="FFMPEG_WIDTH")
    ffmpeg_height: int = Field(default=720, alias="FFMPEG_HEIGHT")
    ffmpeg_fps: int = Field(default=24, alias="FFMPEG_FPS")
    ffmpeg_duration_tolerance_seconds: float = Field(
        default=2.0,
        alias="FFMPEG_DURATION_TOLERANCE_SECONDS",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        if isinstance(value, list):
            return [str(origin) for origin in value]
        raise TypeError("CORS_ORIGINS must be a comma-separated string or list")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
