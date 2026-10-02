from fastapi import APIRouter, Depends
from pymongo.asynchronous.database import AsyncDatabase

from app.db.mongodb import get_db
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    db: AsyncDatabase = Depends(get_db),
) -> TokenResponse:
    return await auth_service.register_user(db, payload)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    db: AsyncDatabase = Depends(get_db),
) -> TokenResponse:
    return await auth_service.login_user(db, payload)


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: UserResponse = Depends(auth_service.get_current_user),
) -> UserResponse:
    return current_user
