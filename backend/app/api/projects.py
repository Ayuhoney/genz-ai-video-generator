from fastapi import APIRouter, Depends, Response, status
from pymongo.asynchronous.database import AsyncDatabase

from app.db.mongodb import get_db
from app.schemas.auth import UserResponse
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate
from app.services import project_service
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    payload: ProjectCreate,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> ProjectResponse:
    return await project_service.create_project(db, current_user, payload)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> list[ProjectResponse]:
    return await project_service.list_projects(db, current_user)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> ProjectResponse:
    return await project_service.get_project(db, current_user, project_id)


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> ProjectResponse:
    return await project_service.update_project(db, current_user, project_id, payload)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    db: AsyncDatabase = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
) -> Response:
    await project_service.delete_project(db, current_user, project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
