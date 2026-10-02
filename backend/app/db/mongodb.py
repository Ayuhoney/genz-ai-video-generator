from collections.abc import AsyncGenerator
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import get_settings

_client: AsyncMongoClient | None = None


async def connect_to_mongo() -> None:
    global _client
    settings = get_settings()
    _client = AsyncMongoClient(settings.mongodb_url)
    await _client.admin.command("ping")
    db = _client[settings.mongodb_db_name]
    await db.users.create_index("email", unique=True)
    await db.projects.create_index("user_id")


async def close_mongo_connection() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_client() -> AsyncMongoClient:
    if _client is None:
        raise RuntimeError("MongoDB client is not initialized")
    return _client


def get_database() -> AsyncDatabase:
    settings = get_settings()
    return get_client()[settings.mongodb_db_name]


async def get_db() -> AsyncGenerator[AsyncDatabase, None]:
    yield get_database()


def serialize_id(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    result = dict(document)
    if "_id" in result:
        result["id"] = str(result.pop("_id"))
    return result
