#!/usr/bin/env python3
"""Probe whether this fal account may disable enable_safety_checker.

Sends one queue request to FAL_VIDEO_MODEL (default: wan turbo) with
enable_safety_checker=false and a known-safe public image. Logs whether the
API accepted the flag, ignored it, or rejected the request.

Usage (from backend/ or worker container):
  export FAL_KEY=...
  python scripts/diagnose_fal_safety_checker.py
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

QUEUE_BASE = "https://queue.fal.run"
SAFE_IMAGE = (
    "https://storage.googleapis.com/falserverless/model_tests/wan/dragon-warrior.jpg"
)
SAFE_PROMPT = (
    "The white dragon warrior stands still, eyes full of determination. "
    "The camera slowly moves closer."
)


def main() -> int:
    key = (os.environ.get("FAL_KEY") or "").strip()
    model = (
        os.environ.get("FAL_VIDEO_MODEL")
        or "fal-ai/wan/v2.2-a14b/image-to-video/turbo"
    ).strip()
    if not key:
        print("FAL_KEY is required", file=sys.stderr)
        return 2

    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    payload = {
        "image_url": SAFE_IMAGE,
        "prompt": SAFE_PROMPT,
        "resolution": "480p",
        "aspect_ratio": "auto",
        "enable_prompt_expansion": False,
        "acceleration": "regular",
        "enable_safety_checker": False,
        "enable_output_safety_checker": False,
    }
    print(f"model={model}")
    print("submitting with enable_safety_checker=false ...")

    with httpx.Client(timeout=60.0) as client:
        submit = client.post(
            f"{QUEUE_BASE}/{model}",
            headers=headers,
            json=payload,
        )
        print(f"SUBMIT status={submit.status_code}")
        print(submit.text[:800])
        if submit.status_code >= 400:
            print(
                "RESULT: submit rejected — account likely cannot disable safety "
                "checker, or request was invalid. Keep FAL_DISABLE_SAFETY_CHECKER=false."
            )
            return 1

        data = submit.json()
        status_url = data["status_url"]
        result_url = data["response_url"]
        request_id = data.get("request_id")
        print(f"request_id={request_id}")

        for i in range(60):
            st = client.get(status_url, headers=headers)
            body = st.json()
            state = body.get("status")
            print(f"poll[{i}] http={st.status_code} status={state}")
            if state == "COMPLETED":
                rr = client.get(result_url, headers=headers)
                print(f"RESULT http={rr.status_code}")
                print(rr.text[:1000])
                if rr.status_code < 400:
                    print(
                        "RESULT: request completed with enable_safety_checker=false. "
                        "This does NOT prove the checker was off (unauthorized accounts "
                        "are always checked). You may set FAL_DISABLE_SAFETY_CHECKER=true "
                        "only if fal support confirms authorization; pipeline still "
                        "assumes checker may be on."
                    )
                    return 0
                print(
                    "RESULT: completed then error — inspect body. Likely content "
                    "policy or validation; disable flag may be ignored."
                )
                return 1
            if state in {"FAILED", "CANCELLED", "CANCELED"}:
                print("RESULT: job failed")
                print(json.dumps(body, indent=2)[:1000])
                return 1
            time.sleep(2)

    print("RESULT: timed out waiting for status")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
