#!/usr/bin/env python3
"""Manual smoke test: generate ONE ~5s image-to-video clip via fal.

Requires env (never committed secrets):
  FAL_KEY
  FAL_IMAGE_MODEL   # from https://fal.ai/models (text-to-image)
  FAL_VIDEO_MODEL   # from https://fal.ai/models (image-to-video)

Optional:
  MEDIA_LOCAL_ROOT=./media-smoke
  FAL_POLL_TIMEOUT_SECONDS=600

Usage:
  cd backend && source .venv/bin/activate
  export FAL_KEY=... FAL_IMAGE_MODEL=... FAL_VIDEO_MODEL=...
  python scripts/smoke_test_real_provider.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Keep default providers mock in app; this script calls fal providers directly.
os.environ.setdefault("MEDIA_LOCAL_ROOT", str(ROOT / "media-smoke"))
os.environ.setdefault("PROVIDER_TIMEOUT_SECONDS", "900")


def main() -> int:
    key = (os.environ.get("FAL_KEY") or "").strip()
    image_model = (os.environ.get("FAL_IMAGE_MODEL") or "").strip()
    video_model = (os.environ.get("FAL_VIDEO_MODEL") or "").strip()
    if not key or not image_model or not video_model:
        print(
            "Set FAL_KEY, FAL_IMAGE_MODEL, and FAL_VIDEO_MODEL "
            "(copy model IDs from https://fal.ai/models). Aborting.",
            file=sys.stderr,
        )
        return 1

    from app.core.config import get_settings
    from app.providers.base import ImageRequest, VideoRequest
    from app.providers.fal import FalImageProvider, FalVideoProvider
    from app.storage import create_storage
    from app.workers.settings import get_worker_settings

    get_settings.cache_clear()
    get_worker_settings.cache_clear()

    print(f"image_model={image_model}")
    print(f"video_model={video_model}")
    print("Generating still…")
    image = FalImageProvider().generate(
        ImageRequest(
            project_id="smoke",
            prompt="cinematic wide shot of a quiet street at golden hour, photorealistic",
            width=768,
            height=432,
        )
    )
    assert image.data
    print(f"image bytes={len(image.data)} cost≈{image.cost_usd}")

    print("Generating 5s clip from still…")
    video = FalVideoProvider().generate(
        VideoRequest(
            project_id="smoke",
            prompt="slow gentle camera push-in, subtle ambient motion",
            duration_seconds=5,
            image_bytes=image.data,
            image_mime=image.mime_type,
        )
    )
    assert video.data
    print(f"video bytes={len(video.data)} cost≈{video.cost_usd} duration={video.duration_seconds}")

    storage = create_storage(media_local_root=os.environ["MEDIA_LOCAL_ROOT"])
    key = "smoke/clip.mp4"
    storage.upload(key, video.data, content_type=video.mime_type)
    out_path = Path(os.environ["MEDIA_LOCAL_ROOT"]) / key
    print(f"Wrote {out_path}")
    print("Smoke test OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
