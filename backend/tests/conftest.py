import os

# Must be set before app imports so settings/cache pick up test values.
os.environ["MONGODB_URL"] = os.environ.get(
    "MONGODB_URL",
    "mongodb://localhost:27017",
)
os.environ["MONGODB_DB_NAME"] = "ai_video_platform_test"
os.environ["JWT_SECRET"] = "test-only-jwt-secret-not-for-production-32b"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["JWT_EXPIRE_MINUTES"] = "60"
os.environ["CORS_ORIGINS"] = "http://localhost:5173"
os.environ["REDIS_URL"] = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
os.environ["REDIS_SSL_CERT_REQS"] = "none"
os.environ["INTERNAL_API_TOKEN"] = "test-internal-token"
os.environ["API_BASE_URL"] = "http://test"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["CELERY_MAX_RETRIES"] = "0"
os.environ["MOCK_TASK_SLEEP_SECONDS"] = "0"
os.environ["MEDIA_LOCAL_ROOT"] = os.environ.get("MEDIA_LOCAL_ROOT", "./media-test")
os.environ["PROVIDER_IMAGE"] = "mock"
os.environ["PROVIDER_VIDEO"] = "mock"
os.environ["PROVIDER_TTS"] = "mock"
os.environ["PROVIDER_SFX"] = "mock"
os.environ["PROVIDER_MUSIC"] = "mock"
os.environ["DIRECTOR_PROVIDER"] = "mock"
os.environ["ALLOW_MOCK"] = "true"

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.db.mongodb import close_mongo_connection, connect_to_mongo, get_database
from app.main import create_app
from app.orchestration.checkpointing import close_checkpointer, init_checkpointer
from app.workers.job_store import close_sync_client
from app.workers.settings import get_worker_settings

get_settings.cache_clear()
get_worker_settings.cache_clear()

# Ensure Celery eager mode after settings load.
from app.workers.celery_app import celery_app

celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True


@pytest.fixture
def orchestration_db() -> Iterator[None]:
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    init_checkpointer(db_name="ai_video_platform_test")
    yield
    close_checkpointer()
    close_sync_client()
    get_settings.cache_clear()
    get_worker_settings.cache_clear()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    await connect_to_mongo()
    init_checkpointer(db_name="ai_video_platform_test")
    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        db = get_database()
        await db.users.delete_many({})
        await db.projects.delete_many({})
        await db.jobs.delete_many({})
        await db.assets.delete_many({})
        yield ac
        await db.users.delete_many({})
        await db.projects.delete_many({})
        await db.jobs.delete_many({})
        await db.assets.delete_many({})
    close_checkpointer()
    close_sync_client()
    await close_mongo_connection()
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    from app.storage import reset_storage_cache
    from app.providers.registry import reset_registry

    reset_storage_cache()
    reset_registry()


@pytest.fixture
def auth_headers_factory():
    def _factory(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    return _factory
