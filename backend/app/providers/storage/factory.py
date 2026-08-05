"""Report storage provider selection, driven entirely by configuration."""

from __future__ import annotations

from app.providers.storage.base import ReportStorageProvider
from app.providers.storage.s3_provider import S3ReportStorageProvider
from app.providers.storage.supabase_provider import SupabaseReportStorageProvider


def build_report_storage_provider(settings) -> ReportStorageProvider:
    """Return S3 when explicitly enabled, otherwise the existing Supabase store.

    Supabase remains the default so ClauseGuard is unaffected until AWS storage
    has been verified in a real environment.
    """
    if settings.aws_report_storage_enabled:
        return S3ReportStorageProvider(
            bucket=settings.aws_s3_report_bucket or "",
            region=settings.aws_region,
            kms_key_id=settings.aws_kms_key_id,
        )
    return SupabaseReportStorageProvider(
        settings.supabase_url,
        settings.supabase_service_role_key,
        bucket=settings.supabase_reports_bucket,
    )
