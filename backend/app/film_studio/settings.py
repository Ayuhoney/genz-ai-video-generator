"""Film studio settings (working Flask pipeline → FastAPI). Secrets from env only."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FilmStudioSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    fal_key: str = Field(default="", alias="FAL_KEY")
    sarvam_key: str = Field(default="", alias="SARVAM_KEY")
    gemini_key: str = Field(default="", alias="GEMINI_KEY")

    gemini_model: str = Field(
        default="gemini-2.0-flash",
        alias="GEMINI_MODEL",
    )
    # Model IDs via env (never commit secrets; examples in .env.example)
    fal_image_model: str = Field(default="", alias="FAL_IMAGE_MODEL")
    fal_video_model: str = Field(default="", alias="FAL_VIDEO_MODEL")
    fal_sfx_model: str = Field(default="", alias="FAL_SFX_MODEL")
    fal_music_model: str = Field(default="", alias="FAL_MUSIC_MODEL")
    fal_video_resolution: str = Field(default="480p", alias="FAL_VIDEO_RESOLUTION")

    film_studio_output_dir: str = Field(
        default="./output",
        alias="FILM_STUDIO_OUTPUT_DIR",
    )
    film_studio_max_scenes: int = Field(default=12, alias="FILM_STUDIO_MAX_SCENES")
    film_studio_parallel: int = Field(default=3, alias="FILM_STUDIO_PARALLEL")
    film_studio_width: int = Field(default=854, alias="FILM_STUDIO_WIDTH")
    film_studio_height: int = Field(default=480, alias="FILM_STUDIO_HEIGHT")
    film_studio_fps: int = Field(default=24, alias="FILM_STUDIO_FPS")

    cost_img: float = Field(default=0.003, alias="FILM_COST_IMG")
    cost_vid: float = Field(default=0.25, alias="FILM_COST_VID")
    cost_sfx: float = Field(default=0.006, alias="FILM_COST_SFX")
    cost_music: float = Field(default=0.01, alias="FILM_COST_MUSIC")
    cost_llm: float = Field(default=0.0, alias="FILM_COST_LLM")


@lru_cache
def get_film_settings() -> FilmStudioSettings:
    return FilmStudioSettings()


def film_output_root() -> Path:
    root = Path(get_film_settings().film_studio_output_dir)
    if not root.is_absolute():
        # Relative to backend cwd
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root
