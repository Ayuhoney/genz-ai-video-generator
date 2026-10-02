# AI Video Platform — Backend

Python 3.11+ FastAPI service for auth, projects, LangGraph orchestration, and
Celery job dispatch. Workers are designed to run on a separate host from the API.

## Deployment model

| Process | Typical host | Needs |
| --- | --- | --- |
| FastAPI + LangGraph | Railway | `MONGODB_URL`, `REDIS_URL` (often `rediss://`), `INTERNAL_API_TOKEN` |
| Celery workers | Separate VPS | Same MongoDB + Redis (worker may use local `redis://`), `INTERNAL_API_TOKEN`, `API_BASE_URL` |
| Redis | VPS (or managed) | Shared broker/result backend |
| MongoDB | Shared | Jobs + checkpoints + app data |
| Cloudflare R2 | Shared | Media keys only (workers never share local disk with the API) |

Workers use temp dirs and return fake/object keys; they must not import FastAPI app state.

## Setup (virtualenv)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — set secrets; never commit .env
```

## Environment

| Variable | Description |
| --- | --- |
| `MONGODB_URL` | MongoDB connection string |
| `MONGODB_DB_NAME` | Database name (default `ai_video_platform`) |
| `JWT_SECRET` | Secret used to sign JWTs |
| `CORS_ORIGINS` | Comma-separated allowed origins |
| `REDIS_URL` | Broker/result URL (`redis://` or `rediss://`). API and workers may differ. |
| `REDIS_SSL_CERT_REQS` | For `rediss://`: `none` \| `optional` \| `required` |
| `INTERNAL_API_TOKEN` | Shared secret for `X-Internal-Token` on worker callbacks |
| `API_BASE_URL` | Base URL workers use to `POST /api/internal/jobs/{id}/complete` |
| `CELERY_TASK_ALWAYS_EAGER` | `true` for in-process tests |
| `CELERY_MAX_RETRIES` | Max task retries (exponential backoff + jitter) |
| `CELERY_SOFT_TIME_LIMIT` / `CELERY_TIME_LIMIT` | Soft/hard Celery time limits (seconds) |
| `MOCK_TASK_SLEEP_SECONDS` | Brief sleep inside mock tasks |

## Run the API

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## FFmpeg (workers)

Celery workers **must** have `ffmpeg` and `ffprobe` on `PATH`. On worker startup the process
exits immediately if either is missing.

```bash
# Debian/Ubuntu
sudo apt update && sudo apt install -y ffmpeg

ffmpeg -version && ffprobe -version
```

Scene composition and final assembly run only in workers (temp dirs, R2 download/upload).

Optional tuning (see `.env.example`): `FFMPEG_WIDTH`, `FFMPEG_HEIGHT`, `FFMPEG_FPS`,
`FFMPEG_DURATION_TOLERANCE_SECONDS`.

## Run a Celery worker

Prefer the VPS Docker stack (`deploy/vps/`) for production. For local Redis:

Start Redis first (`docker compose up -d` from repo root).

```bash
cd backend
source .venv/bin/activate
# Linux/macOS
celery -A app.workers.celery_app.celery_app worker --loglevel=INFO -Q media,audio,ffmpeg,celery

# Windows (prefork pool is unsupported)
celery -A app.workers.celery_app.celery_app worker --loglevel=INFO --pool=solo -Q media,audio,ffmpeg,celery
```

Production images: `backend/Dockerfile` (Railway API), `backend/Dockerfile.worker` (VPS Celery + FFmpeg). See root `README.md` → Deployment.

When a job finishes, the worker calls:

`POST {API_BASE_URL}/api/internal/jobs/{job_id}/complete` with header `X-Internal-Token`.

If the callback fails, `GET /api/projects/{id}/production/status` reconciles job state from MongoDB and can resume the graph.

## Tests

```bash
source .venv/bin/activate
pytest -q
```

Tests force `CELERY_TASK_ALWAYS_EAGER=true` and use DB `ai_video_platform_test`.

## Cloudflare R2 + providers

Generation goes through `app/providers` (default `mock`). Set `PROVIDER_IMAGE`,
`PROVIDER_VIDEO`, `PROVIDER_TTS`, `PROVIDER_SFX`, `PROVIDER_MUSIC` and optional
`PROVIDER_*_FALLBACKS` comma lists.

Media is uploaded via `app/storage`: Cloudflare R2 when `R2_ACCOUNT_ID`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET` are set; otherwise
local `MEDIA_LOCAL_ROOT` (default `./media`). Bytes are never stored in MongoDB —
only `assets` metadata + cost.

Signed URL: `GET /api/projects/{id}/assets/{assetId}/url` (auth + ownership).

## fal.ai image / video (optional)

Default providers remain `mock`. To use fal queue APIs:

1. Set `FAL_KEY` and copy **exact** model IDs from the [fal model gallery](https://fal.ai/models) into `FAL_IMAGE_MODEL` / `FAL_VIDEO_MODEL` (never hardcode in app code).
2. Set `PROVIDER_IMAGE=fal` and/or `PROVIDER_VIDEO=fal` (optional `PROVIDER_*_FALLBACKS=mock`).
3. Raise `PROVIDER_TIMEOUT_SECONDS` to at least `FAL_POLL_TIMEOUT_SECONDS` (long video jobs).
4. Manual smoke (one 5s clip): `python scripts/smoke_test_real_provider.py`

Queue flow: `POST https://queue.fal.run/{model}` → poll `status_url` → GET result → download media → upload to R2/local → `assets` + cost. Budget: `MAX_PROJECT_COST_USD` → project status `paused_budget`.

## AI Film Studio (FastAPI port of the working Flask script)

Open `http://127.0.0.1:8000/studio` after starting the API.

Requires `.env`: `FAL_KEY`, `SARVAM_KEY`, `GEMINI_KEY`, plus `FAL_IMAGE_MODEL` / `FAL_VIDEO_MODEL` / `FAL_SFX_MODEL` / `FAL_MUSIC_MODEL` (see `.env.example`). Needs `ffmpeg` on PATH.

Flow: Gemini director → fal keyframe → fal i2v → fal SFX + Sarvam voice → music → FFmpeg mix → `film.mp4`.

## API surface (this step)

- Auth / projects / director (previous steps)
- `POST /api/projects/{id}/production/start|resume|regenerate`
- `GET /api/projects/{id}/production/status` (reconcile fallback)
- `GET /api/projects/{id}/jobs`
- `GET /api/projects/{id}/assets`
- `GET /api/projects/{id}/assets/{assetId}/url`
- `POST /api/internal/jobs/{job_id}/complete` (`X-Internal-Token`)
- `GET /api/health`
