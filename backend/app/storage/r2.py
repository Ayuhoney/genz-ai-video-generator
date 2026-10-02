"""Cloudflare R2 storage via boto3 S3-compatible API."""

from __future__ import annotations

from typing import Any, BinaryIO

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from app.storage.base import StorageBackend


def build_r2_client(
    *,
    account_id: str,
    access_key_id: str,
    secret_access_key: str,
    endpoint_url: str | None = None,
) -> BaseClient:
    endpoint = endpoint_url or f"https://{account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


class R2Storage(StorageBackend):
    def __init__(
        self,
        *,
        bucket: str,
        client: BaseClient | None = None,
        account_id: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        endpoint_url: str | None = None,
        public_base_url: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.public_base_url = (public_base_url or "").rstrip("/") or None
        if client is not None:
            self.client = client
        else:
            if not account_id or not access_key_id or not secret_access_key:
                raise ValueError("R2 credentials required when client is not provided")
            self.client = build_r2_client(
                account_id=account_id,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                endpoint_url=endpoint_url,
            )

    def upload(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str = "application/octet-stream",
    ) -> str:
        body: Any = data
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        return key

    def download(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        if self.public_base_url:
            # Still return a private signed URL; public base is optional CDN hint.
            pass
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=max(1, expires_in),
        )
