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
    id: str
    name: str
    description: str
    role: str


class Scene(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: str
    order: int
    title: str
    description: str
    duration_seconds: int = Field(serialization_alias="durationSeconds")
    status: ProductionStatus


class DirectorResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    title: str
    concept: str
    characters: list[Character]
    story_structure: list[str] = Field(serialization_alias="storyStructure")
    scenes: list[Scene]
    estimated_duration_seconds: int = Field(
        serialization_alias="estimatedDurationSeconds",
    )
