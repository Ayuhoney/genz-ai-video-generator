#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

FRONTEND_PID=""
BACKEND_PID=""

cleanup() {
  echo ""
  echo "Shutting down..."

  if [[ -n "${FRONTEND_PID}" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi

  if [[ -n "${BACKEND_PID}" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi

  echo "Stopped."
}

trap cleanup EXIT INT TERM

echo "==> Preparing backend..."
cd "$ROOT_DIR/backend"
if [[ ! -d .venv ]]; then
  echo "Creating Python virtualenv..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

if [[ ! -f .env ]]; then
  echo "Creating backend/.env from .env.example (edit secrets if needed)..."
  cp .env.example .env
fi

echo "==> Starting backend (FastAPI) — uses Atlas Mongo + VPS Redis from .env..."
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

echo "==> Preparing frontend..."
cd "$ROOT_DIR/frontend"
if [[ ! -d node_modules ]]; then
  echo "Installing frontend dependencies..."
  NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-https://registry.npmjs.org}" npm install
fi

if [[ ! -f .env ]]; then
  echo "Creating frontend/.env from .env.example..."
  cp .env.example .env
fi

echo "==> Starting frontend (Vite)..."
npm run dev -- --host 0.0.0.0 --port 5173 &
FRONTEND_PID=$!

echo ""
echo "Services starting (no local Docker Redis/Mongo)."
echo "  Backend:  http://localhost:8000"
echo "  Studio:   http://localhost:8000/studio"
echo "  API docs: http://localhost:8000/docs"
echo "  Frontend: http://localhost:5173"
echo ""
echo "Optional Celery worker (another terminal):"
echo "  cd backend && source .venv/bin/activate"
echo "  celery -A app.workers.celery_app.celery_app worker --loglevel=INFO -Q media,audio,ffmpeg,celery"
echo ""
echo "Press Ctrl+C to stop."
echo ""

wait -n "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || wait
