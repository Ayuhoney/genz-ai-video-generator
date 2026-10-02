# ai-video-platform

## Where things run now

| Piece | Where | Notes |
| --- | --- | --- |
| Frontend + FastAPI + LangGraph | Your laptop | `uvicorn` / `npm run dev` |
| **Celery + FFmpeg (heavy)** | **VPS** `200.234.41.50` | containers `genzai-video-*` under `/opt/genz-ai-video` |
| Media files | **VPS disk** volume | public `http://200.234.41.50:9100/...` (not R2) |
| Redis | VPS (existing) DB **`/2`** | isolated from other apps |
| Mongo | Atlas `genz_ai_video` | |
| Existing Hostinger apps | `/opt/genzcine` | **untouched** (`rampwalk`, `news-agent`) |

## Run locally (API only)

```bash
cd backend && source .venv/bin/activate
# .env already points R2_PUBLIC_BASE_URL at VPS media
uvicorn app.main:app --reload --port 8000
```

Worker is already on the VPS — **do not** need a local Celery process for production jobs.

## VPS ops (isolated)

```bash
ssh genzcine-vps
cd /opt/genz-ai-video/deploy/vps
docker compose ps
docker compose logs -f worker
# update after code change (from your laptop):
# rsync backend + deploy/vps, then: docker compose up -d --build
```

Fill `FAL_KEY` / `SARVAM_KEY` / `GEMINI_KEY` in both `backend/.env` and `deploy/vps/.env`, then recreate worker.
