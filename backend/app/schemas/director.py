from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProductionStatus = Literal["pending", "running", "completed", "failed"]


class DirectorGenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    idea: str = Field(min_length=1)
    duration_seconds: int = Field(ge=10, le=1800, alias="durationSeconds")
    language: str
    genre: str
    instructions: str = ""


class Character(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    name: str
    description: str
    role: str
    reference_image_url: str | None = Field(
        default=None,
        alias="referenceImageUrl",
        serialization_alias="referenceImageUrl",
    )
    face_locked: bool = Field(
        default=False,
        alias="faceLocked",
        serialization_alias="faceLocked",
    )


class ShotPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    order: int
    title: str
    description: str
    duration_seconds: float = Field(
        serialization_alias="durationSeconds",
        alias="durationSeconds",
    )
    camera: str = ""


class VoiceLine(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    character_id: str | None = Field(
        default=None,
        alias="characterId",
        serialization_alias="characterId",
    )
    character_name: str = Field(
        default="Narrator",
        alias="characterName",
        serialization_alias="characterName",
    )
    text: str
    estimated_seconds: float = Field(
        default=3.0,
        alias="estimatedSeconds",
        serialization_alias="estimatedSeconds",
    )


class Scene(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    order: int
    title: str
    description: str
    duration_seconds: int = Field(
        serialization_alias="durationSeconds",
        alias="durationSeconds",
    )
    status: ProductionStatus
    shots: list[ShotPlan] = Field(default_factory=list)
    voice_over: list[VoiceLine] = Field(
        default_factory=list,
        alias="voiceOver",
        serialization_alias="voiceOver",
    )
    sfx_notes: str = Field(
        default="",
        alias="sfxNotes",
        serialization_alias="sfxNotes",
    )


class DirectorResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    title: str
    concept: str
    script: str = ""
    characters: list[Character]
    story_structure: list[str] = Field(serialization_alias="storyStructure")
    scenes: list[Scene]
    estimated_duration_seconds: int = Field(
        serialization_alias="estimatedDurationSeconds",
    )
