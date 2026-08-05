"""AWS S3 report storage and SES email delivery.

No network access: botocore's Stubber validates every call against the real
service model, so a wrong parameter name or shape fails the test the same way
AWS would reject it.
"""

import io

import boto3
import pytest
from botocore.stub import ANY, Stubber

from app.core.config import Settings
from app.providers.email.base import Attachment, EmailMessage
from app.providers.email.providers import SesEmailProvider, build_email_provider
from app.providers.storage.base import ReportStorageProvider
from app.providers.storage.factory import build_report_storage_provider
from app.providers.storage.s3_provider import S3ReportStorageProvider, build_s3_client
from app.providers.storage.supabase_provider import SupabaseReportStorageProvider

BUCKET = "clauseguard-reports-test"
REGION = "us-east-1"
ORG = "aaaaaaaa-0000-0000-0000-00000000000a"
ANALYSIS = "11111111-1111-1111-1111-111111111111"
KEY = f"{ORG}/{ANALYSIS}/report-v1.pdf"
PDF = b"%PDF-1.4 test report bytes"
CHECKSUM = "a" * 64


def s3_provider(kms: str | None = None):
    # Built exactly as production does, so SigV4 presigning is what we test.
    client = build_s3_client(
        REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",  # noqa: S106
    )
    provider = S3ReportStorageProvider(BUCKET, REGION, kms_key_id=kms, client=client)
    return provider, Stubber(client)


def base_settings(**overrides) -> Settings:
    defaults = {
        "_env_file": None,
        "environment": "test",
        "supabase_url": "https://x.supabase.co",
        "supabase_service_role_key": "k",
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestProviderSelection:
    def test_supabase_is_the_default(self):
        """AWS must be opt-in; ClauseGuard is unaffected until it is enabled."""
        provider = build_report_storage_provider(base_settings())
        assert isinstance(provider, SupabaseReportStorageProvider)
        assert provider.name == "supabase"

    def test_s3_is_used_when_the_flag_is_on(self):
        provider = build_report_storage_provider(
            base_settings(aws_report_storage_enabled=True, aws_s3_report_bucket=BUCKET)
        )
        assert isinstance(provider, S3ReportStorageProvider)
        assert provider.name == "s3"

    def test_enabling_s3_without_a_bucket_fails_at_startup(self):
        with pytest.raises(ValueError, match="AWS_S3_REPORT_BUCKET"):
            base_settings(aws_report_storage_enabled=True)

    def test_enabling_ses_without_a_sender_fails_at_startup(self):
        with pytest.raises(ValueError, match="AWS_SES_FROM_EMAIL"):
            base_settings(aws_ses_enabled=True)

    def test_ses_takes_precedence_over_email_provider(self):
        provider = build_email_provider(
            base_settings(
                aws_ses_enabled=True,
                aws_ses_from_email="reports@example.com",
                email_provider="console",
            )
        )
        assert provider.name == "ses"

    def test_console_remains_the_default_when_ses_is_off(self):
        assert build_email_provider(base_settings()).name == "console"

    def test_both_providers_satisfy_the_interface(self):
        for cls in (S3ReportStorageProvider, SupabaseReportStorageProvider):
            assert issubclass(cls, ReportStorageProvider)


class TestS3Upload:
    @pytest.mark.asyncio
    async def test_upload_requests_encryption_and_pdf_content_type(self):
        provider, stub = s3_provider()
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "Body": PDF,
                "ContentType": "application/pdf",
                "Metadata": ANY,
                "ServerSideEncryption": "AES256",
            },
        )
        with stub:
            key = await provider.upload_report(
                KEY, PDF, analysis_id=ANALYSIS, org_id=ORG, version=1, checksum=CHECKSUM
            )
        assert key == KEY
        stub.assert_no_pending_responses()

    @pytest.mark.asyncio
    async def test_kms_encryption_is_used_when_configured(self):
        provider, stub = s3_provider(kms="arn:aws:kms:us-east-1:1:key/abc")
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": BUCKET,
                "Key": KEY,
                "Body": PDF,
                "ContentType": "application/pdf",
                "Metadata": ANY,
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": "arn:aws:kms:us-east-1:1:key/abc",
            },
        )
        with stub:
            await provider.upload_report(
                KEY, PDF, analysis_id=ANALYSIS, org_id=ORG, version=1, checksum=CHECKSUM
            )
        stub.assert_no_pending_responses()

    def test_object_key_is_organization_scoped(self):
        from app.services.report_storage import build_report_key

        key = build_report_key(ORG, ANALYSIS, 1)
        assert key.startswith(f"{ORG}/")
        assert key.endswith("report-v1.pdf")

    def test_versions_never_overwrite_each_other(self):
        from app.services.report_storage import build_report_key

        assert build_report_key(ORG, ANALYSIS, 1) != build_report_key(ORG, ANALYSIS, 2)

    def test_metadata_contains_only_safe_identifiers(self):
        """No title, vendor, filename, email or evidence text may reach S3."""
        provider, _ = s3_provider()
        metadata = provider.safe_metadata(
            analysis_id=ANALYSIS, org_id=ORG, version=1, checksum=CHECKSUM
        )
        assert set(metadata) == {
            "analysis-id",
            "org-id",
            "report-version",
            "checksum-sha256",
            "generated-at",
        }
        blob = " ".join(metadata.values()).lower()
        for leak in ("@", "acme", ".pdf", "liability", "quote"):
            assert leak not in blob

    def test_bucket_is_required(self):
        with pytest.raises(ValueError, match="AWS_S3_REPORT_BUCKET"):
            S3ReportStorageProvider("", REGION)


class TestS3Download:
    @pytest.mark.asyncio
    async def test_presigned_url_is_short_lived(self):
        provider, _ = s3_provider()
        url = await provider.create_download_url(KEY, 900)
        assert url.startswith("https://")
        assert "X-Amz-Expires=900" in url
        assert "X-Amz-Signature=" in url

    @pytest.mark.asyncio
    async def test_ttl_is_clamped_to_a_safe_ceiling(self):
        provider, _ = s3_provider()
        long_url = await provider.create_download_url(KEY, 86_400)
        short_url = await provider.create_download_url(KEY, 1)
        assert "X-Amz-Expires=3600" in long_url, "must not issue a day-long link"
        assert "X-Amz-Expires=60" in short_url

    @pytest.mark.asyncio
    async def test_presigned_url_is_not_permanent_or_public(self):
        provider, _ = s3_provider()
        url = await provider.create_download_url(KEY, 900)
        assert "X-Amz-Expires" in url and "X-Amz-Credential" in url

    @pytest.mark.asyncio
    async def test_download_returns_the_bytes(self):
        provider, stub = s3_provider()
        stub.add_response(
            "get_object",
            {"Body": io.BytesIO(PDF)},
            {"Bucket": BUCKET, "Key": KEY},
        )
        with stub:
            assert await provider.download_report(KEY) == PDF

    @pytest.mark.asyncio
    async def test_missing_object_reports_absent_rather_than_raising(self):
        provider, stub = s3_provider()
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
        with stub:
            assert await provider.exists(KEY) is False

    @pytest.mark.asyncio
    async def test_existing_object_is_detected(self):
        provider, stub = s3_provider()
        stub.add_response(
            "head_object", {"ContentLength": len(PDF)}, {"Bucket": BUCKET, "Key": KEY}
        )
        with stub:
            assert await provider.exists(KEY) is True

    @pytest.mark.asyncio
    async def test_metadata_round_trip(self):
        provider, stub = s3_provider()
        stub.add_response(
            "head_object",
            {
                "ContentLength": len(PDF),
                "ContentType": "application/pdf",
                "Metadata": {"checksum-sha256": CHECKSUM},
            },
            {"Bucket": BUCKET, "Key": KEY},
        )
        with stub:
            meta = await provider.get_metadata(KEY)
        assert meta is not None
        assert meta.checksum == CHECKSUM
        assert meta.provider == "s3"

    @pytest.mark.asyncio
    async def test_missing_metadata_returns_none(self):
        provider, stub = s3_provider()
        stub.add_client_error("head_object", service_error_code="NoSuchKey", http_status_code=404)
        with stub:
            assert await provider.get_metadata(KEY) is None

    @pytest.mark.asyncio
    async def test_unexpected_s3_error_is_raised_not_swallowed(self):
        from botocore.exceptions import ClientError

        provider, stub = s3_provider()
        stub.add_client_error(
            "head_object", service_error_code="AccessDenied", http_status_code=403
        )
        with stub, pytest.raises(ClientError):
            await provider.exists(KEY)


def ses_provider(**kwargs):
    client = boto3.client(
        "sesv2", region_name=REGION, aws_access_key_id="test", aws_secret_access_key="test"
    )
    provider = SesEmailProvider(REGION, "reports@example.com", client=client, **kwargs)
    return provider, Stubber(client)


class TestSes:
    @pytest.mark.asyncio
    async def test_send_succeeds_and_returns_the_message_id(self):
        provider, stub = ses_provider()
        stub.add_response(
            "send_email",
            {"MessageId": "0100018f-ses-id"},
            {
                "FromEmailAddress": "reports@example.com",
                "Destination": {"ToAddresses": ["alice@example.com"]},
                "Content": {"Raw": {"Data": ANY}},
            },
        )
        with stub:
            result = await provider.send(
                EmailMessage(to="alice@example.com", subject="s", text_body="t")
            )
        assert result.ok is True
        assert result.provider == "ses"
        assert result.message_id == "0100018f-ses-id"

    @pytest.mark.asyncio
    async def test_configuration_set_is_included_when_set(self):
        provider, stub = ses_provider(configuration_set="clauseguard-metrics")
        stub.add_response(
            "send_email",
            {"MessageId": "id"},
            {
                "FromEmailAddress": "reports@example.com",
                "Destination": {"ToAddresses": ["a@b.com"]},
                "Content": {"Raw": {"Data": ANY}},
                "ConfigurationSetName": "clauseguard-metrics",
            },
        )
        with stub:
            assert (await provider.send(EmailMessage(to="a@b.com", subject="s", text_body="t"))).ok
        stub.assert_no_pending_responses()

    def test_attachments_are_included_in_the_mime_payload(self):
        provider, _ = ses_provider()
        raw = provider.build_mime(
            EmailMessage(
                to="a@b.com",
                subject="s",
                text_body="t",
                attachments=[Attachment(filename="report.pdf", content=PDF)],
            )
        )
        assert b"report.pdf" in raw
        assert b"application/pdf" in raw

    def test_html_alternative_is_included(self):
        provider, _ = ses_provider()
        raw = provider.build_mime(
            EmailMessage(to="a@b.com", subject="s", text_body="t", html_body="<p>hi</p>")
        )
        assert b"text/html" in raw

    @pytest.mark.asyncio
    async def test_failure_returns_a_safe_error_not_an_exception(self):
        provider, stub = ses_provider()
        stub.add_client_error(
            "send_email", service_error_code="MessageRejected", http_status_code=400
        )
        with stub:
            result = await provider.send(
                EmailMessage(to="alice@example.com", subject="s", text_body="t")
            )
        assert result.ok is False
        assert result.error_code == "MessageRejected"
        # No provider internals, and no recipient, in what reaches the caller.
        assert "alice" not in (result.error_message_safe or "")
        assert "Traceback" not in (result.error_message_safe or "")

    @pytest.mark.asyncio
    async def test_throttling_is_reported_as_a_failure_not_a_crash(self):
        provider, stub = ses_provider()
        stub.add_client_error("send_email", service_error_code="Throttling", http_status_code=429)
        with stub:
            result = await provider.send(EmailMessage(to="a@b.com", subject="s", text_body="t"))
        assert result.ok is False and result.error_code == "Throttling"

    def test_sender_is_required(self):
        with pytest.raises(ValueError, match="AWS_SES_FROM_EMAIL"):
            SesEmailProvider(REGION, "")

    def test_provider_does_not_choose_the_recipient(self):
        """The address is always supplied by ReportDeliveryService."""
        import inspect

        source = inspect.getsource(SesEmailProvider)
        assert "message.to" in source
        for forbidden in ("profiles", "SELECT email", "request."):
            assert forbidden not in source


class TestFailureIsolation:
    @pytest.mark.asyncio
    async def test_s3_failure_does_not_touch_analysis_state(self):
        """No AWS code path may write to analyses, findings, or scores."""
        import inspect

        import app.providers.email.providers as email_module
        import app.providers.storage.s3_provider as s3_module

        for module in (s3_module, email_module):
            source = inspect.getsource(module)
            for forbidden in (
                "UPDATE analyses",
                "analyses.complete",
                "machine_severity",
                "findings",
            ):
                assert forbidden not in source, f"{module.__name__} must not touch {forbidden}"

    def test_worker_report_delivery_remains_wrapped(self):
        """The existing try/except that isolates report failure must survive."""
        import inspect

        import app.worker as worker_module

        source = inspect.getsource(worker_module.Worker._deliver_report)
        assert "except Exception" in source
        assert "the analysis itself is unaffected" in source
