from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.db.mongodb import get_db, serialize_id
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse

bearer_scheme = HTTPBearer(auto_error=False)


def _user_response(document: dict[str, Any]) -> UserResponse:
    serialized = serialize_id(document)
    assert serialized is not None
    return UserResponse(
        id=serialized["id"],
        email=serialized["email"],
        name=serialized["name"],
    )


async def register_user(db: AsyncDatabase, payload: RegisterRequest) -> TokenResponse:
    document = {
        "email": payload.email.lower(),
        "name": payload.name.strip(),
        "password_hash": hash_password(payload.password),
        "created_at": datetime.now(UTC),
    }
    try:
        result = await db.users.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        ) from exc

    document["_id"] = result.inserted_id
    user = _user_response(document)
    token = create_access_token(user.id, {"email": user.email})
    return TokenResponse(access_token=token, user=user)


async def login_user(db: AsyncDatabase, payload: LoginRequest) -> TokenResponse:
    document = await db.users.find_one({"email": payload.email.lower()})
    if document is None or not verify_password(
        payload.password,
        document["password_hash"],
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    user = _user_response(document)
    token = create_access_token(user.id, {"email": user.email})
    return TokenResponse(access_token=token, user=user)


async def get_user_by_id(db: AsyncDatabase, user_id: str) -> UserResponse:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    document = await db.users.find_one({"_id": ObjectId(user_id)})
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    return _user_response(document)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncDatabase = Depends(get_db),
) -> UserResponse:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    try:
        payload = decode_access_token(credentials.credentials)
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
            )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc

    return await get_user_by_id(db, subject)
