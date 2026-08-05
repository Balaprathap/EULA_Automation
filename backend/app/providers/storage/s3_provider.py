"""Amazon S3 report storage.

Design notes:

  * The bucket must be private with public access blocked. Nothing here creates
    a public URL - downloads use short-lived pre-signed URLs, and those URLs are
    never persisted or logged.
  * Server-side encryption is always requested: SSE-KMS when AWS_KMS_KEY_ID is
    configured, otherwise SSE-S3 (AES256).
  * Object metadata carries only safe identifiers - analysis id, org id,
    version, checksum, timestamp. No document title, vendor, filename, email or
    evidence text ever reaches S3 metadata.
  * boto3 is synchronous, so every call runs in a worker thread to keep the
    event loop responsive.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.providers.storage.base import ReportObjectMetadata, ReportStorageProvider

logger = get_logger(__name__)

CONTENT_TYPE = "application/pdf"
NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


def build_s3_client(region: str, **overrides):
    """Construct the S3 client.

    Exposed so tests build a client identically to production - in particular
    with signature_version="s3v4". Without it boto3 can fall back to the
    deprecated SigV2 presigning scheme, which produces long-lived
    ``AWSAccessKeyId``/``Expires`` URLs instead of ``X-Amz-*`` SigV4 ones.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=region,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
        ),
        **overrides,
    )


class S3ReportStorageProvider(ReportStorageProvider):
    def __init__(
        self,
        bucket: str,
        region: str,
        *,
        kms_key_id: str | None = None,
        client: Any = None,
    ) -> None:
        if not bucket:
            raise ValueError(
                "AWS_S3_REPORT_BUCKET is required when AWS_REPORT_STORAGE_ENABLED is true."
            )
        self.bucket = bucket
        self.region = region
        self.kms_key_id = kms_key_id
        self._client = client

    @property
    def name(self) -> str:
        return "s3"

    def _get_client(self) -> Any:
        if self._client is None:
            # Credentials come from the deployment environment (IAM role, or the
            # standard AWS_* variables). Nothing is read from application config.
            self._client = build_s3_client(self.region)
        return self._client

    def _encryption_args(self) -> dict[str, Any]:
        if self.kms_key_id:
            return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms_key_id}
        return {"ServerSideEncryption": "AES256"}

    def safe_metadata(
        self, *, analysis_id: str, org_id: str, version: int, checksum: str
    ) -> dict[str, str]:
        """Only non-identifying operational values. Kept public for testing."""
        return {
            "analysis-id": str(analysis_id),
            "org-id": str(org_id),
            "report-version": str(version),
            "checksum-sha256": checksum,
            "generated-at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    # -- ReportStorageProvider --------------------------------------------
    async def upload_report(
        self,
        key: str,
        pdf_bytes: bytes,
        *,
        analysis_id: str,
        org_id: str,
        version: int,
        checksum: str,
    ) -> str:
        metadata = self.safe_metadata(
            analysis_id=analysis_id, org_id=org_id, version=version, checksum=checksum
        )

        def _put() -> None:
            self._get_client().put_object(
                Bucket=self.bucket,
                Key=key,
                Body=pdf_bytes,
                ContentType=CONTENT_TYPE,
                Metadata=metadata,
                **self._encryption_args(),
            )

        await asyncio.to_thread(_put)
        logger.info(
            "report uploaded to s3",
            extra={
                "analysis_id": analysis_id,
                "bytes": len(pdf_bytes),
                "version": version,
                "encryption": "kms" if self.kms_key_id else "aes256",
            },
        )
        return key

    async def create_download_url(self, key: str, ttl_seconds: int) -> str:
        """Short-lived pre-signed URL. Never persisted, never logged."""
        ttl = max(60, min(int(ttl_seconds), 3600))

        def _sign() -> str:
            return self._get_client().generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=ttl,
            )

        url = await asyncio.to_thread(_sign)
        # Deliberately logs the TTL, never the URL itself.
        logger.info("presigned report url issued", extra={"ttl_seconds": ttl})
        return url

    async def download_report(self, key: str) -> bytes:
        def _get() -> bytes:
            response = self._get_client().get_object(Bucket=self.bucket, Key=key)
            return bytes(response["Body"].read())

        return await asyncio.to_thread(_get)

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            from botocore.exceptions import ClientError

            try:
                self._get_client().head_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in NOT_FOUND_CODES:
                    return False
                raise

        return await asyncio.to_thread(_head)

    async def get_metadata(self, key: str) -> ReportObjectMetadata | None:
        def _head() -> ReportObjectMetadata | None:
            from botocore.exceptions import ClientError

            try:
                response = self._get_client().head_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in NOT_FOUND_CODES:
                    return None
                raise
            meta = response.get("Metadata") or {}
            return ReportObjectMetadata(
                key=key,
                size_bytes=response.get("ContentLength"),
                content_type=response.get("ContentType", CONTENT_TYPE),
                checksum=meta.get("checksum-sha256"),
                provider=self.name,
            )

        return await asyncio.to_thread(_head)

    async def delete_report(self, key: str) -> None:
        def _delete() -> None:
            self._get_client().delete_object(Bucket=self.bucket, Key=key)

        await asyncio.to_thread(_delete)
