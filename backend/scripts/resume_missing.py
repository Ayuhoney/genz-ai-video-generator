#!/usr/bin/env python3
"""Inventory + resume missing assets (reuse completed; cost estimate gate).

Usage (estimate only):
  python scripts/resume_missing.py --project-id 6ac0bcc4ab9a4aa5f04adddc

Run after reviewing cost:
  CONFIRM_RESUME=true python scripts/resume_missing.py \\
    --project-id 6ac0bcc4ab9a4aa5f04adddc --confirm

Force regenerate specific shots (pays again for those only):
  CONFIRM_RESUME=true python scripts/resume_missing.py \\
    --project-id 6ac0bcc4ab9a4aa5f04adddc --confirm --force shot-1,shot-2
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resume production: missing assets only + cost gate",
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Enqueue work (or set CONFIRM_RESUME=true)",
    )
    parser.add_argument(
        "--force",
        default="",
        help="Comma-separated shot ids to regenerate even if present",
    )
    parser.add_argument(
        "--no-assemble",
        action="store_true",
        help="Skip scene_render/final_assembly even when shots are complete",
    )
    args = parser.parse_args()

    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

    from app.workers.resume_missing import build_inventory, format_inventory_table, run_resume_missing

    force = [s.strip() for s in args.force.split(",") if s.strip()]
    if not args.confirm:
        inv = build_inventory(args.project_id)
        print(format_inventory_table(inv), flush=True)
        print(
            f"\nESTIMATED_MISSING_USD={inv.estimated_cost_usd:.4f} "
            f"(re-run with --confirm or CONFIRM_RESUME=true to start)",
            flush=True,
        )
        return 0

    result = run_resume_missing(
        args.project_id,
        force_shot_ids=force,
        confirm=True,
        assemble=not args.no_assemble,
    )
    print(result["report"], flush=True)
    print(
        f"\nESTIMATED_MISSING_USD={result['estimated_cost_usd']:.4f} "
        f"started={result['started']} assembled={result['assembled']} "
        f"jobs={len(result.get('job_ids') or [])}",
        flush=True,
    )
    print(result.get("message") or "", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
