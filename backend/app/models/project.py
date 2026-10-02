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


class ProjectDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    user_id: str
    title: str
    status: ProjectStatus = "draft"
    duration_seconds: int = 60
    language: str = "Hindi"
    genre: str = "Drama"
    idea: str | None = None
    concept: str | None = None
    director_response: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.utcnow())
    updated_at: datetime = Field(default_factory=lambda: datetime.utcnow())

    def to_mongo(self) -> dict[str, Any]:
        return self.model_dump(exclude={"id"})
