"""Private Supabase Storage access.

Objects are keyed ``{org_id}/{document_id}/{filename}``. The bucket is private;
nothing here ever produces a public URL. Downloads use short-lived signed URLs
minted only after the API has confirmed the caller owns the document.
"""

from __future__ import annotations

import re

import httpx

from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

SIGNED_URL_TTL_SECONDS = 300  # 5 minutes
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def build_object_key(org_id: str, document_id: str, filename: str) -> str:
    """Org-prefixed key. The prefix is what the storage RLS policy checks."""
    safe_name = _UNSAFE.sub("_", (filename or "document").strip())[:120] or "document"
    return f"{org_id}/{document_id}/{safe_name}"


class StorageService:
    def __init__(self, supabase_url: str, service_role_key: str, bucket: str = "documents") -> None:
        self.base_url = supabase_url.rstrip("/")
        self._key = service_role_key
        self.bucket = bucket

    def _headers(self, content_type: str | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self._key}",
            "apikey": self._key,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        url = f"{self.base_url}/storage/v1/object/{self.bucket}/{key}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url, headers={**self._headers(content_type), "x-upsert": "true"}, content=data
            )
        if response.status_code >= 400:
            logger.error("storage upload failed", extra={"status": response.status_code})
            raise AppError(
                "The file could not be stored.", code="STORAGE_UPLOAD_FAILED", status_code=502
            )
        return key

    async def create_signed_url(self, key: str, ttl: int = SIGNED_URL_TTL_SECONDS) -> str:
        url = f"{self.base_url}/storage/v1/object/sign/{self.bucket}/{key}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url, headers=self._headers("application/json"), json={"expiresIn": ttl}
            )
        if response.status_code >= 400:
            raise AppError(
                "A download link could not be generated.",
                code="STORAGE_SIGN_FAILED",
                status_code=502,
            )
        signed = response.json().get("signedURL", "")
        return f"{self.base_url}/storage/v1{signed}"

    async def delete(self, key: str) -> None:
        url = f"{self.base_url}/storage/v1/object/{self.bucket}/{key}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.delete(url, headers=self._headers())
