from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProjectStatus = Literal[
    "draft",
    "review",
    "confirmed",
    "producing",
    "completed",
    "failed",
]


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    duration_seconds: int = Field(default=60, ge=10, le=1800, alias="durationSeconds")
    language: str = "Hindi"
    genre: str = "Drama"
    idea: str | None = None
    concept: str | None = None
    status: ProjectStatus = "draft"
    director_response: dict[str, Any] | None = Field(
        default=None,
        alias="directorResponse",
    )

    model_config = ConfigDict(populate_by_name=True)


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    duration_seconds: int | None = Field(
        default=None,
        ge=10,
        le=1800,
        alias="durationSeconds",
    )
    language: str | None = None
    genre: str | None = None
    idea: str | None = None
    concept: str | None = None
    status: ProjectStatus | None = None
    director_response: dict[str, Any] | None = Field(
        default=None,
        alias="directorResponse",
    )

    model_config = ConfigDict(populate_by_name=True)


class ProjectResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True, populate_by_name=True)

    id: str
    title: str
    status: ProjectStatus
    duration_seconds: int = Field(serialization_alias="durationSeconds")
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")
    language: str
    genre: str
    idea: str | None = None
    concept: str | None = None
    director_response: dict[str, Any] | None = Field(
        default=None,
        serialization_alias="directorResponse",
    )
    final_video_asset_id: str | None = Field(
        default=None,
        serialization_alias="finalVideoAssetId",
    )
