from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from fastapi import HTTPException, status
from pymongo.asynchronous.database import AsyncDatabase

from app.db.mongodb import serialize_id
from app.schemas.auth import UserResponse
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate


def _to_project_response(document: dict[str, Any]) -> ProjectResponse:
    serialized = serialize_id(document)
    assert serialized is not None
    return ProjectResponse(
        id=serialized["id"],
        title=serialized["title"],
        status=serialized["status"],
        duration_seconds=serialized["duration_seconds"],
        created_at=serialized["created_at"],
        updated_at=serialized["updated_at"],
        language=serialized["language"],
        genre=serialized["genre"],
        idea=serialized.get("idea"),
        concept=serialized.get("concept"),
        director_response=serialized.get("director_response"),
        final_video_asset_id=serialized.get("final_video_asset_id"),
    )


def _parse_object_id(project_id: str) -> ObjectId:
    if not ObjectId.is_valid(project_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return ObjectId(project_id)


async def create_project(
    db: AsyncDatabase,
    user: UserResponse,
    payload: ProjectCreate,
) -> ProjectResponse:
    now = datetime.now(UTC)
    director_response = payload.director_response
    if isinstance(director_response, dict) and director_response:
        try:
            from app.schemas.director import DirectorGenerateRequest
            from app.services.director_validate import director_plan_from_dict

            validated = director_plan_from_dict(
                director_response,
                DirectorGenerateRequest(
                    idea=payload.idea or payload.title,
                    duration_seconds=payload.duration_seconds,
                    language=payload.language,
                    genre=payload.genre,
                    instructions="",
                ),
            )
            director_response = validated.model_dump(by_alias=True)
        except Exception:
            # Keep original payload if re-validation fails; generate path already validates.
            pass

    document = {
        "user_id": user.id,
        "title": payload.title,
        "status": payload.status,
        "duration_seconds": payload.duration_seconds,
        "language": payload.language,
        "genre": payload.genre,
        "idea": payload.idea,
        "concept": payload.concept,
        "director_response": director_response,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.projects.insert_one(document)
    document["_id"] = result.inserted_id
    return _to_project_response(document)


async def list_projects(db: AsyncDatabase, user: UserResponse) -> list[ProjectResponse]:
    cursor = db.projects.find({"user_id": user.id}).sort("created_at", -1)
    return [_to_project_response(doc) async for doc in cursor]


async def get_project(
    db: AsyncDatabase,
    user: UserResponse,
    project_id: str,
) -> ProjectResponse:
    object_id = _parse_object_id(project_id)
    document = await db.projects.find_one({"_id": object_id})
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    if document["user_id"] != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project",
        )
    return _to_project_response(document)


async def update_project(
    db: AsyncDatabase,
    user: UserResponse,
    project_id: str,
    payload: ProjectUpdate,
) -> ProjectResponse:
    object_id = _parse_object_id(project_id)
    document = await db.projects.find_one({"_id": object_id})
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    if document["user_id"] != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project",
        )

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return _to_project_response(document)

    updates["updated_at"] = datetime.now(UTC)
    await db.projects.update_one({"_id": object_id}, {"$set": updates})
    updated = await db.projects.find_one({"_id": object_id})
    assert updated is not None
    return _to_project_response(updated)


async def delete_project(
    db: AsyncDatabase,
    user: UserResponse,
    project_id: str,
) -> None:
    object_id = _parse_object_id(project_id)
    document = await db.projects.find_one({"_id": object_id})
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    if document["user_id"] != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project",
        )
    await db.projects.delete_one({"_id": object_id})
