from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    email: EmailStr
    name: str
    password_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.utcnow())

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(exclude={"id"})
        return data
