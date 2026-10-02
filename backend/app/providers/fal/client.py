"""fal.ai queue REST client (submit → poll status → fetch result).

Based on https://fal.ai/docs/documentation/model-apis/inference/queue
Auth: Authorization: Key $FAL_KEY
"""

from __future__ import annotations

import time
from typing import Any

import httpx

QUEUE_BASE = "https://queue.fal.run"
STORAGE_INITIATE = "https://rest.fal.ai/storage/upload/initiate"


class FalAPIError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FalClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 60.0,
        poll_interval: float = 2.0,
        poll_timeout: float = 600.0,
        webhook_url: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise FalAPIError("FAL_KEY is required for fal providers")
        self.api_key = api_key
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.webhook_url = (webhook_url or "").strip() or None
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> FalClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Key {self.api_key}",
            "Content-Type": "application/json",
        }

    def submit(self, model_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """POST https://queue.fal.run/{model_id} — returns request_id + URLs."""
        url = f"{QUEUE_BASE}/{model_id.lstrip('/')}"
        params: dict[str, str] = {}
        if self.webhook_url:
            params["fal_webhook"] = self.webhook_url
        response = self._client.post(
            url,
            headers=self._headers(),
            params=params or None,
            json=arguments,
        )
        if response.status_code >= 400:
            raise FalAPIError(
                f"fal submit failed ({response.status_code}): {response.text[:500]}",
                status_code=response.status_code,
            )
        data = response.json()
        if not data.get("request_id"):
            raise FalAPIError(f"fal submit missing request_id: {data}")
        return data

    def get_status(self, status_url: str, *, logs: bool = False) -> dict[str, Any]:
        params = {"logs": "1"} if logs else None
        response = self._client.get(
            status_url,
            headers=self._headers(),
            params=params,
        )
        if response.status_code >= 400:
            raise FalAPIError(
                f"fal status failed ({response.status_code}): {response.text[:500]}",
                status_code=response.status_code,
            )
        return response.json()

    def get_result(self, result_url: str) -> dict[str, Any]:
        """GET result/response URL. 202 means still running."""
        response = self._client.get(result_url, headers=self._headers())
        if response.status_code == 202:
            raise FalAPIError("fal result not ready (202)", status_code=202)
        if response.status_code >= 400:
            raise FalAPIError(
                f"fal result failed ({response.status_code}): {response.text[:500]}",
                status_code=response.status_code,
            )
        return response.json()

    def run(
        self,
        model_id: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Submit and poll until COMPLETED. Returns (result, status_metrics)."""
        submitted = self.submit(model_id, arguments)
        request_id = submitted["request_id"]
        status_url = submitted.get("status_url") or (
            f"{QUEUE_BASE}/{model_id}/requests/{request_id}/status"
        )
        # Official docs use both /requests/{id} and response_url.
        result_url = (
            submitted.get("response_url")
            or submitted.get("result_url")
            or f"{QUEUE_BASE}/{model_id}/requests/{request_id}"
        )

        deadline = time.monotonic() + self.poll_timeout
        metrics: dict[str, Any] = {}
        while time.monotonic() < deadline:
            status = self.get_status(status_url, logs=False)
            state = status.get("status")
            if state == "COMPLETED":
                if status.get("error"):
                    raise FalAPIError(
                        f"fal request failed: {status.get('error')} "
                        f"({status.get('error_type')})"
                    )
                metrics = dict(status.get("metrics") or {})
                result = self.get_result(result_url)
                return result, metrics
            if state in {"FAILED", "CANCELLED", "CANCELED"}:
                raise FalAPIError(f"fal request ended with status={state}: {status}")
            # IN_QUEUE / IN_PROGRESS — keep polling
            time.sleep(self.poll_interval)

        raise FalAPIError(
            f"fal poll timed out after {self.poll_timeout}s "
            f"(request_id={request_id})"
        )

    def upload_bytes(
        self,
        data: bytes,
        *,
        content_type: str,
        file_name: str = "upload.bin",
    ) -> str:
        """Upload to fal CDN via REST initiate + PUT (for image_url inputs)."""
        init = self._client.post(
            STORAGE_INITIATE,
            headers=self._headers(),
            params={"storage_type": "fal-cdn-v3"},
            json={"file_name": file_name, "content_type": content_type},
        )
        if init.status_code >= 400:
            raise FalAPIError(
                f"fal upload initiate failed ({init.status_code}): {init.text[:500]}",
                status_code=init.status_code,
            )
        payload = init.json()
        upload_url = payload.get("upload_url") or payload.get("url")
        file_url = payload.get("file_url") or payload.get("access_url")
        if not upload_url:
            raise FalAPIError(f"fal upload initiate missing upload_url: {payload}")

        put = self._client.put(
            upload_url,
            content=data,
            headers={"Content-Type": content_type},
        )
        if put.status_code >= 400:
            raise FalAPIError(
                f"fal upload PUT failed ({put.status_code}): {put.text[:500]}",
                status_code=put.status_code,
            )
        if file_url:
            return str(file_url)
        # Some responses only return upload_url; file URL may be in body after PUT.
        try:
            body = put.json()
            return str(body.get("access_url") or body.get("file_url") or body.get("url"))
        except Exception as exc:
            raise FalAPIError("fal upload did not return a file URL") from exc

    def download(self, url: str) -> bytes:
        response = self._client.get(url, follow_redirects=True)
        if response.status_code >= 400:
            raise FalAPIError(
                f"download failed ({response.status_code}) for {url[:120]}",
                status_code=response.status_code,
            )
        return response.content

    def get_account_billing(self) -> dict[str, Any]:
        """GET https://api.fal.ai/v1/account/billing?expand=credits"""
        response = self._client.get(
            "https://api.fal.ai/v1/account/billing",
            headers=self._headers(),
            params={"expand": "credits"},
        )
        if response.status_code >= 400:
            raise FalAPIError(
                f"fal billing failed ({response.status_code}): {response.text[:400]}",
                status_code=response.status_code,
            )
        return response.json()


def extract_image_url(result: dict[str, Any]) -> str:
    images = result.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict) and first.get("url"):
            return str(first["url"])
        if isinstance(first, str):
            return first
    image = result.get("image")
    if isinstance(image, dict) and image.get("url"):
        return str(image["url"])
    if isinstance(result.get("url"), str):
        return str(result["url"])
    raise FalAPIError(f"No image URL in fal result keys={list(result.keys())}")


def extract_video_url(result: dict[str, Any]) -> str:
    video = result.get("video")
    if isinstance(video, dict) and video.get("url"):
        return str(video["url"])
    if isinstance(video, str):
        return video
    videos = result.get("videos")
    if isinstance(videos, list) and videos:
        first = videos[0]
        if isinstance(first, dict) and first.get("url"):
            return str(first["url"])
    if isinstance(result.get("video_url"), str):
        return str(result["video_url"])
    raise FalAPIError(f"No video URL in fal result keys={list(result.keys())}")
