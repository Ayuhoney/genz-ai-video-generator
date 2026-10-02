from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import assets, auth, director, internal, production, projects
from app.core.config import get_settings
from app.db.mongodb import close_mongo_connection, connect_to_mongo
from app.film_studio import router as film_studio_router
from app.orchestration.checkpointing import close_checkpointer, init_checkpointer
from app.storage.asset_store import ensure_asset_indexes
from app.workers.job_store import close_sync_client, ensure_job_indexes, get_db


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await connect_to_mongo()
    init_checkpointer()
    settings = get_settings()
    sync_db = get_db(settings.mongodb_url, settings.mongodb_db_name)
    ensure_job_indexes(sync_db)
    ensure_asset_indexes(sync_db)
    try:
        yield
    finally:
        close_checkpointer()
        close_sync_client()
        await close_mongo_connection()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI Video Platform API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(director.router)
    app.include_router(production.router)
    app.include_router(assets.router)
    app.include_router(internal.router)
    app.include_router(film_studio_router, prefix="/studio")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
