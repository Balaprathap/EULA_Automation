"""Adapter exposing the existing Supabase storage as a ReportStorageProvider.

This is the fallback and the current default. `ReportStorageService` itself is
unchanged - this only conforms it to the shared interface so the two clouds are
interchangeable.
"""

from __future__ import annotations

from app.providers.storage.base import ReportObjectMetadata, ReportStorageProvider
from app.services.report_storage import ReportStorageService


class SupabaseReportStorageProvider(ReportStorageProvider):
    def __init__(self, supabase_url: str, service_role_key: str, bucket: str = "reports") -> None:
        self._service = ReportStorageService(supabase_url, service_role_key, bucket=bucket)
        self.bucket = bucket

    @property
    def name(self) -> str:
        return "supabase"

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
        # Supabase Storage carries no custom object metadata; the same values are
        # already persisted on the analysis_reports row.
        return await self._service.upload(key, pdf_bytes)

    async def create_download_url(self, key: str, ttl_seconds: int) -> str:
        return await self._service.signed_url(key, ttl_seconds)

    async def download_report(self, key: str) -> bytes:
        return await self._service.download(key)

    async def exists(self, key: str) -> bool:
        try:
            await self._service.download(key)
            return True
        except Exception:  # noqa: BLE001 - absence is the only thing we report
            return False

    async def get_metadata(self, key: str) -> ReportObjectMetadata | None:
        return ReportObjectMetadata(key=key, provider=self.name)
