from fastapi import APIRouter, Depends

from app.schemas.auth import UserResponse
from app.schemas.director import DirectorGenerateRequest, DirectorResponse
from app.services import director_service
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/director", tags=["director"])


@router.post("/generate", response_model=DirectorResponse)
async def generate_director(
    payload: DirectorGenerateRequest,
    _: UserResponse = Depends(get_current_user),
) -> DirectorResponse:
    return await director_service.generate_director_response(payload)
