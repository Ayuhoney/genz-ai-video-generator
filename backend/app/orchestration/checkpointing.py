from __future__ import annotations

from typing import Any

from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient

from app.core.config import get_settings

_client: MongoClient | None = None
_checkpointer: MongoDBSaver | None = None


def get_checkpointer() -> MongoDBSaver:
    if _checkpointer is None:
        raise RuntimeError("MongoDB checkpointer is not initialized")
    return _checkpointer


def init_checkpointer(
    *,
    mongodb_url: str | None = None,
    db_name: str | None = None,
) -> MongoDBSaver:
    """Create a persistent MongoDBSaver (langgraph-checkpoint-mongodb).

    Official package: ``langgraph-checkpoint-mongodb``
    Import: ``from langgraph.checkpoint.mongodb import MongoDBSaver``
    AsyncMongoDBSaver was removed; use MongoDBSaver async methods (aget/aput/...).
    """
    global _client, _checkpointer

    settings = get_settings()
    url = mongodb_url or settings.mongodb_url
    name = db_name or settings.mongodb_db_name

    close_checkpointer()
    _client = MongoClient(url)
    _checkpointer = MongoDBSaver(
        _client,
        db_name=name,
        checkpoint_collection_name="lg_checkpoints",
        writes_collection_name="lg_checkpoint_writes",
    )
    return _checkpointer


def close_checkpointer() -> None:
    global _client, _checkpointer
    if _checkpointer is not None:
        try:
            _checkpointer.close()
        except Exception:
            pass
        _checkpointer = None
    if _client is not None:
        _client.close()
        _client = None


def thread_config(project_id: str, **extra: Any) -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": project_id}
    configurable.update(extra)
    return {"configurable": configurable}
