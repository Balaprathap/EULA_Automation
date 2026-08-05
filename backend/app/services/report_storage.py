"""Private storage for generated PDF reports.

Reuses the existing StorageService rather than introducing a second storage
client. Keys are `{org_id}/{analysis_id}/report-v{n}.pdf`: the organization id
must be the FIRST path segment, because that is what the storage RLS policy
checks - the same convention migration 0009 established for source documents.

No permanent public URL is ever produced. Downloads go through the authenticated
API, which streams the bytes or mints a short-lived signed URL.
"""

from __future__ import annotations

import re

from app.core.logging import get_logger
from app.services.storage import StorageService

logger = get_logger(__name__)

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_DOT_RUN = re.compile(r"\.{2,}")


def _safe_segment(value: str) -> str:
    """One path segment: no separators, no dot-runs, no leading dots.

    `/` is already replaced, so traversal is impossible either way, but
    collapsing `..` removes the ambiguity entirely rather than relying on that.
    """
    segment = _UNSAFE.sub("_", str(value))
    segment = _DOT_RUN.sub("_", segment).lstrip(".")
    return segment[:64] or "unknown"


def build_report_key(org_id: str, analysis_id: str, version: int) -> str:
    """Deterministic, sanitized object key. Distinct versions never collide."""
    return (
        f"{_safe_segment(org_id)}/{_safe_segment(analysis_id)}/report-v{max(1, int(version))}.pdf"
    )


def build_download_filename(document_title: str, analysis_id: str) -> str:
    """User-facing filename for the Content-Disposition header."""
    stem = _UNSAFE.sub("-", (document_title or "agreement").strip())[:60].strip("-_.")
    if not stem:
        stem = "agreement"
    return f"clauseguard-{stem}-{str(analysis_id)[:8]}.pdf"


class ReportStorageService:
    def __init__(self, supabase_url: str, service_role_key: str, bucket: str = "reports") -> None:
        self._storage = StorageService(supabase_url, service_role_key, bucket=bucket)
        self.bucket = bucket

    async def upload(self, key: str, pdf_bytes: bytes) -> str:
        return await self._storage.upload(key, pdf_bytes, "application/pdf")

    async def signed_url(self, key: str, ttl: int) -> str:
        return await self._storage.create_signed_url(key, ttl=ttl)

    async def download(self, key: str) -> bytes:
        """Fetch the stored PDF via a short-lived signed URL, server-side."""
        import httpx

        url = await self._storage.create_signed_url(key, ttl=60)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
        response.raise_for_status()
        return response.content


class ProviderStorageAdapter:
    """Adapts a ReportStorageProvider to the shape ReportDeliveryService expects.

    ReportDeliveryService is unchanged: it still calls upload / signed_url /
    download. This shim lets either cloud back those calls, and records which
    provider actually served them so the UI and analytics can report it.
    """

    def __init__(self, provider, *, analysis_id: str = "", org_id: str = "", version: int = 1):
        self._provider = provider
        self._analysis_id = analysis_id
        self._org_id = org_id
        self._version = version

    @property
    def provider_name(self) -> str:
        return self._provider.name

    async def upload(self, key: str, pdf_bytes: bytes) -> str:
        from app.services.report_generator import checksum_of

        return await self._provider.upload_report(
            key,
            pdf_bytes,
            analysis_id=self._analysis_id,
            org_id=self._org_id,
            version=self._version,
            checksum=checksum_of(pdf_bytes),
        )

    async def signed_url(self, key: str, ttl: int) -> str:
        return await self._provider.create_download_url(key, ttl)

    async def download(self, key: str) -> bytes:
        return await self._provider.download_report(key)
