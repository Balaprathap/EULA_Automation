"""Report storage provider abstraction.

The existing Supabase-backed `ReportStorageService` and the new S3 provider both
satisfy this interface, so `ReportDeliveryService` never needs to know which
cloud a report lives in. AWS SDK calls are confined to the S3 implementation.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class ReportObjectMetadata:
    key: str
    size_bytes: int | None = None
    content_type: str = "application/pdf"
    checksum: str | None = None
    provider: str = "unknown"


class ReportStorageProvider(abc.ABC):
    """Private storage for generated PDF reports. No provider returns a
    permanent public URL - downloads are short-lived or server-streamed."""

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @abc.abstractmethod
    async def upload_report(
        self,
        key: str,
        pdf_bytes: bytes,
        *,
        analysis_id: str,
        org_id: str,
        version: int,
        checksum: str,
    ) -> str: ...

    @abc.abstractmethod
    async def create_download_url(self, key: str, ttl_seconds: int) -> str: ...

    @abc.abstractmethod
    async def download_report(self, key: str) -> bytes: ...

    @abc.abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abc.abstractmethod
    async def get_metadata(self, key: str) -> ReportObjectMetadata | None: ...

    async def delete_report(self, key: str) -> None:
        """Optional. Default is a no-op so providers need not support deletion."""
        return None
