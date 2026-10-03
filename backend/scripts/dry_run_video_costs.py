#!/usr/bin/env python3
"""DRY_RUN cost preview for video clips — no fal spend.

Usage:
  DRY_RUN=true FAL_KEY=dummy python scripts/dry_run_video_costs.py \\
    --clips 4 --duration 10 --project-id preview
"""

from __future__ import annotations

import argparse
import os
import sys

# Force dry-run before settings import.
os.environ["DRY_RUN"] = "true"
os.environ.setdefault("FAL_KEY", "dry-run-no-key")
os.environ.setdefault(
    "FAL_VIDEO_MODEL",
    "fal-ai/wan/v2.2-a14b/image-to-video/turbo",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Estimate fal video costs (DRY_RUN)")
    parser.add_argument("--clips", type=int, default=4)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--project-id", default="dry-run-preview")
    parser.add_argument("--prompt", default="cinematic slow push-in")
    args = parser.parse_args()

    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

    from app.core.config import get_settings
    from app.providers.fal.adapters import parse_model_chain
    from app.providers.fal.client import FalClient
    from app.providers.fal.generate_clip import ClipGenerateRequest, generate_clip
    from app.workers.settings import get_worker_settings

    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    s = get_worker_settings()
    chain = parse_model_chain(s.fal_video_model_chain, primary=s.fal_video_model)
    total = 0.0
    print(f"DRY_RUN=true models={chain}", flush=True)
    with FalClient("dry-run", poll_interval=0.01, poll_timeout=1.0) as client:
        for i in range(max(1, args.clips)):
            clip = generate_clip(
                client,
                ClipGenerateRequest(
                    model_id=chain[0],
                    prompt=f"{args.prompt} clip-{i+1}",
                    image_url="https://storage.googleapis.com/falserverless/model_tests/wan/dragon-warrior.jpg",
                    duration_seconds=args.duration,
                    resolution=s.fal_video_resolution,
                    clip_id=f"{args.project_id}:scene-{i+1}:shot-1",
                    project_id=args.project_id,
                    model_chain=chain,
                ),
            )
            total += float(clip.estimated_cost_usd or 0.0)
    print(
        f"[DRY_RUN] JOB_TOTAL clips={args.clips} duration_each={args.duration}s "
        f"estimated_job_usd={total:.4f} JOB_MAX_USD={s.job_max_usd}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
