"""PDF report generation, private storage, and email delivery.

Covers the guarantees that matter: the recipient can only ever be the
authenticated user, a worker retry cannot send twice, an email failure cannot
change the analysis status, and quarantined findings are never presented as
confirmed.
"""

import io

import pytest
from pypdf import PdfReader

from app.providers.email.base import (
    Attachment,
    EmailMessage,
    EmailProvider,
    SendResult,
    hash_email,
    mask_email,
)
from app.providers.email.providers import ConsoleEmailProvider, build_email_provider
from app.services.report_delivery import (
    ReportDeliveryService,
    build_email_bodies,
    severity_counts_from,
)
from app.services.report_generator import ReportContext, ReportGenerator, checksum_of, esc
from app.services.report_storage import build_download_filename, build_report_key

ORG_A = "aaaaaaaa-0000-0000-0000-00000000000a"
ORG_B = "bbbbbbbb-0000-0000-0000-00000000000b"
ANALYSIS_A = "11111111-1111-1111-1111-111111111111"


def make_context(status="complete", **overrides):
    analysis = {
        "id": ANALYSIS_A,
        "org_id": ORG_A,
        "status": status,
        "overall_score": 68.0,
        "risk_band": "elevated",
        "finding_count": 2,
        "review_count": 0,
        "quarantine_count": 1,
        "verification_pass_rate": 66.7,
        "degraded_retrieval": False,
        "input_tokens": 48000,
        "cached_input_tokens": 0,
        "output_tokens": 9600,
        "estimated_cost_usd": 0.29,
        "model_used": "test-model",
    }
    analysis.update(overrides)
    return ReportContext(
        analysis=analysis,
        document={"title": "Acme Cloud EULA", "vendor_name": "Acme"},
        policy={"name": "Default Compliance Policy", "version": 3},
        findings=[
            {
                "category": "limitation_of_liability",
                "effective_severity": "critical",
                "machine_severity": "critical",
                "model_confidence": 0.94,
                "severity_weight": 0.9,
                "verification_status": "verified",
                "verification_method": "offset_exact",
                "plain_summary": "Liability capped at USD 50.",
                "why_it_matters": "Material loss would be uncompensated.",
                "quote": "SHALL NOT EXCEED FIFTY UNITED STATES DOLLARS",
                "doc_start_offset": 10,
                "doc_end_offset": 54,
                "scoring_explanation": "confidence 0.94 x weight 0.90 = 0.85; maps to critical",
                "review_status": "pending",
            },
            {
                "category": "data_retention",
                "effective_severity": "high",
                "machine_severity": "medium",
                "override_severity": "high",
                "model_confidence": 0.71,
                "severity_weight": 0.75,
                "verification_status": "verified",
                "verification_method": "offset_normalized",
                "plain_summary": "Data kept indefinitely.",
                "why_it_matters": "No deletion right.",
                "quote": "may retain Customer Data indefinitely",
                "scoring_explanation": "confidence 0.71 x weight 0.75",
                "review_status": "escalated",
            },
            {
                "category": "indemnification",
                "effective_severity": "high",
                "machine_severity": "high",
                "model_confidence": 0.8,
                "severity_weight": 0.85,
                "verification_status": "quarantined",
                "quarantine_reason": "The proposed quote does not appear in the cited chunk.",
                "plain_summary": "Customer indemnifies the vendor.",
                "why_it_matters": "One-sided obligation.",
                "quote": "THIS TEXT WAS INVENTED BY THE MODEL",
                "scoring_explanation": "n/a",
            },
        ],
        categories=[
            {
                "category": "arbitration",
                "status": "needs_review",
                "needs_review_reason": "The AI provider was unavailable.",
            }
        ],
    )


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


def flat(data: bytes) -> str:
    """PDF text with whitespace collapsed, so line wrapping cannot break a check."""
    return " ".join(pdf_text(data).split())


class TestReportGeneration:
    def test_completed_analysis_generates_a_pdf(self):
        pdf = ReportGenerator().generate(make_context("complete"))
        assert pdf.startswith(b"%PDF-")
        assert len(pdf) > 2000

    def test_report_contains_the_required_sections(self):
        text = pdf_text(ReportGenerator().generate(make_context()))
        for required in [
            "ClauseGuard",
            "Acme Cloud EULA",
            "Acme",
            ANALYSIS_A,
            "Default Compliance Policy",
            "version 3",
            "68.0",
            "elevated",
            "Verified findings",
            "Needs human review",
            "Quarantined",
            "NOT LEGAL ADVICE",
            "Generated",
        ]:
            assert required in text, f"missing from the report: {required}"

    def test_partial_analysis_generates_a_pdf_with_a_warning(self):
        text = flat(ReportGenerator().generate(make_context("partial")))
        assert "Partial analysis" in text
        assert "not a complete pass" in text

    def test_degraded_retrieval_warning_is_included(self):
        context = make_context(degraded_retrieval=True, degraded_reason="Vector search was down.")
        text = pdf_text(ReportGenerator().generate(context))
        assert "Degraded retrieval" in text
        assert "Vector search was down." in text

    def test_findings_are_grouped_by_severity(self):
        text = pdf_text(ReportGenerator().generate(make_context()))
        assert "CRITICAL" in text and "HIGH" in text

    def test_each_finding_carries_its_scoring_inputs(self):
        text = pdf_text(ReportGenerator().generate(make_context()))
        for required in [
            "Machine severity",
            "Effective severity",
            "Confidence",
            "Severity calculation",
            "Why it matters",
        ]:
            assert required in text

    def test_human_override_is_shown(self):
        text = pdf_text(ReportGenerator().generate(make_context()))
        assert "Human override" in text
        assert "escalated" in text

    def test_token_usage_and_cost_are_included(self):
        text = pdf_text(ReportGenerator().generate(make_context()))
        assert "Input tokens" in text
        assert "Estimated cost" in text

    def test_usage_section_is_omitted_when_unavailable(self):
        context = make_context()
        context.analysis["input_tokens"] = None
        assert "Input tokens" not in pdf_text(ReportGenerator().generate(context))

    def test_empty_findings_still_produces_a_valid_report(self):
        context = make_context()
        context.findings = []
        text = pdf_text(ReportGenerator().generate(context))
        assert "No verified findings" in text

    def test_checksum_is_stable_for_identical_bytes(self):
        data = b"abc"
        assert checksum_of(data) == checksum_of(data)
        assert len(checksum_of(data)) == 64


class TestQuarantinedFindings:
    """Quarantined evidence must never look like a confirmed finding."""

    def test_quarantined_section_is_separate_and_labelled(self):
        text = flat(ReportGenerator().generate(make_context()))
        assert "Quarantined - unsupported evidence" in text
        assert "not confirmed findings" in text

    def test_quarantined_finding_states_it_is_excluded_from_scoring(self):
        text = flat(ReportGenerator().generate(make_context()))
        assert "excluded from the risk score" in text

    def test_quarantined_quote_is_not_presented_as_verified(self):
        text = pdf_text(ReportGenerator().generate(make_context()))
        quarantine_index = text.index("Quarantined - unsupported evidence")
        tail = text[quarantine_index:]
        assert "Unsupported evidence" in tail
        # The invented quote must not be rendered as a verified source quote.
        assert "Verified in the source document" not in tail


class TestEscaping:
    def test_markup_in_user_content_is_escaped(self):
        assert esc("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"
        assert esc(None) == ""

    def test_injected_markup_is_rendered_literally(self):
        context = make_context()
        context.document["title"] = "<b>NOT BOLD</b>"
        assert "<b>NOT BOLD</b>" in pdf_text(ReportGenerator().generate(context))


class TestStorageKeys:
    def test_key_is_org_prefixed_for_rls(self):
        key = build_report_key(ORG_A, ANALYSIS_A, 1)
        assert key.startswith(f"{ORG_A}/"), "org id must be the first path segment"
        assert key.endswith("report-v1.pdf")

    def test_versions_do_not_overwrite_each_other(self):
        assert build_report_key(ORG_A, ANALYSIS_A, 1) != build_report_key(ORG_A, ANALYSIS_A, 2)

    def test_keys_are_sanitized(self):
        key = build_report_key("../../etc", "a/b/../c", 1)
        prefix = key.rsplit("/", 1)[0]
        assert ".." not in prefix, "dot-runs must be collapsed"
        assert not prefix.startswith("."), "no leading dot segment"
        assert key.count("/") == 2, "exactly two separators: org/analysis/file"

    def test_empty_segments_do_not_collapse_the_key(self):
        key = build_report_key("", "", 1)
        assert key == "unknown/unknown/report-v1.pdf"

    def test_download_filename_is_sanitized_and_pdf(self):
        name = build_download_filename('Acme "Cloud"/EULA <2026>', ANALYSIS_A)
        assert name.endswith(".pdf")
        for bad in ['"', "/", "<", ">"]:
            assert bad not in name


class TestRecipientResolution:
    def test_email_hash_is_stable_and_case_insensitive(self):
        assert hash_email("A@B.com") == hash_email("a@b.com  ")
        assert len(hash_email("a@b.com")) == 64

    def test_mask_hides_the_local_part(self):
        assert mask_email("alice@example.com") == "a***@example.com"
        assert mask_email("") == "***"
        assert mask_email("nonsense") == "***"

    def test_resend_endpoint_has_no_recipient_parameter(self):
        """A client must not be able to choose who receives the report."""
        import inspect

        from app.api.v1.reports import resend_report_email

        params = set(inspect.signature(resend_report_email).parameters)
        for forbidden in ("email", "recipient", "to", "address", "payload", "body"):
            assert forbidden not in params, f"resend must not accept '{forbidden}'"

    def test_resend_uses_the_authenticated_identity(self):
        import inspect

        from app.api.v1 import reports

        source = inspect.getsource(reports.resend_report_email)
        assert "recipient_email=user.email" in source


class FakeReports:
    def __init__(self, existing=None):
        self.rows = existing or {}
        self.ready_calls = 0

    async def upsert_pending(self, org_id, analysis_id, version=1):
        key = (analysis_id, version)
        row = self.rows.get(key)
        if row is None:
            row = {
                "id": f"report-{version}",
                "org_id": org_id,
                "analysis_id": analysis_id,
                "version": version,
                "generation_status": "generating",
                "storage_path": None,
            }
            self.rows[key] = row
        return dict(row)

    async def mark_ready(self, report_id, *, storage_path, file_size, checksum):
        self.ready_calls += 1
        for row in self.rows.values():
            if row["id"] == report_id:
                row.update(
                    generation_status="ready",
                    storage_path=storage_path,
                    file_size=file_size,
                    checksum=checksum,
                )
                return dict(row)
        return None

    async def mark_failed(self, report_id, code, message):
        for row in self.rows.values():
            if row["id"] == report_id:
                row.update(generation_status="failed", error_code=code)


class FakeDeliveries:
    def __init__(self):
        self.rows = []
        self.claims = 0

    async def claim(self, **kwargs):
        self.claims += 1
        live = [
            r
            for r in self.rows
            if r["recipient_email_hash"] == kwargs["recipient_email_hash"]
            and r["analysis_id"] == kwargs["analysis_id"]
            and r["status"] in ("pending", "sending", "sent")
        ]
        if live:
            return None  # partial unique index would reject this insert
        row = {"id": f"d{len(self.rows)}", "status": "pending", "attempt_count": 0, **kwargs}
        self.rows.append(row)
        return dict(row)

    async def mark_sending(self, delivery_id):
        for r in self.rows:
            if r["id"] == delivery_id:
                r["status"] = "sending"
                r["attempt_count"] += 1

    async def mark_sent(self, delivery_id, *, provider, message_id, mode):
        for r in self.rows:
            if r["id"] == delivery_id:
                r.update(status="sent", provider=provider, delivery_mode=mode)

    async def mark_failed(self, delivery_id, *, provider, code, message, permanent):
        for r in self.rows:
            if r["id"] == delivery_id:
                r.update(status="permanently_failed" if permanent else "failed", error_code=code)

    async def latest_for_analysis(self, org_id, analysis_id):
        rows = [r for r in self.rows if r["analysis_id"] == analysis_id]
        return dict(rows[-1]) if rows else None

    async def reopen_for_resend(self, delivery_id):
        for r in self.rows:
            if r["id"] == delivery_id:
                r["status"] = "pending"


class FakeStorage:
    def __init__(self, fail_download=False):
        self.uploaded = {}
        self.fail_download = fail_download

    async def upload(self, key, data):
        self.uploaded[key] = data
        return key

    async def download(self, key):
        if self.fail_download:
            raise RuntimeError("storage unavailable")
        return self.uploaded[key]

    async def signed_url(self, key, ttl):
        return f"https://storage.example/{key}?token=short-lived&ttl={ttl}"


class RecordingProvider(EmailProvider):
    def __init__(self, fail_times=0):
        self.sent: list[EmailMessage] = []
        self.fail_times = fail_times
        self.attempts = 0

    @property
    def name(self):
        return "recording"

    async def send(self, message):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            return SendResult(
                ok=False, provider=self.name, error_code="TEMP", error_message_safe="temporary"
            )
        self.sent.append(message)
        return SendResult(ok=True, provider=self.name, message_id=f"msg-{self.attempts}")


def build_service(provider=None, storage=None, deliveries=None, reports=None, **kwargs):
    return ReportDeliveryService(
        reports=reports or FakeReports(),
        deliveries=deliveries or FakeDeliveries(),
        storage=storage or FakeStorage(),
        email_provider=provider or RecordingProvider(),
        max_attempts=kwargs.pop("max_attempts", 3),
        **kwargs,
    )


class TestGenerationAndStorage:
    @pytest.mark.asyncio
    async def test_report_is_generated_and_stored_privately(self):
        storage = FakeStorage()
        reports = FakeReports()
        service = build_service(storage=storage, reports=reports)
        report = await service.generate_and_store(
            org_id=ORG_A, analysis_id=ANALYSIS_A, context=make_context()
        )
        assert report["generation_status"] == "ready"
        assert report["storage_path"].startswith(f"{ORG_A}/")
        assert storage.uploaded[report["storage_path"]].startswith(b"%PDF-")

    @pytest.mark.asyncio
    async def test_regeneration_is_skipped_when_already_ready(self):
        reports = FakeReports()
        service = build_service(reports=reports)
        await service.generate_and_store(
            org_id=ORG_A, analysis_id=ANALYSIS_A, context=make_context()
        )
        await service.generate_and_store(
            org_id=ORG_A, analysis_id=ANALYSIS_A, context=make_context()
        )
        assert reports.ready_calls == 1, "a retry must not regenerate an existing report"


class TestEmailDelivery:
    @pytest.mark.asyncio
    async def test_email_is_sent_once_after_completion(self):
        provider = RecordingProvider()
        deliveries = FakeDeliveries()
        service = build_service(provider=provider, deliveries=deliveries)
        report = await service.generate_and_store(
            org_id=ORG_A, analysis_id=ANALYSIS_A, context=make_context()
        )
        outcome = await service.send_report_email(
            org_id=ORG_A,
            analysis_id=ANALYSIS_A,
            report=report,
            recipient_email="alice@example.com",
            recipient_user_id="u1",
            document_title="Acme Cloud EULA",
            analysis=make_context().analysis,
            severity_counts={"critical": 1},
        )
        assert outcome.email_sent is True
        assert len(provider.sent) == 1
        assert provider.sent[0].subject == "ClauseGuard analysis complete - Acme Cloud EULA"

    @pytest.mark.asyncio
    async def test_worker_retry_does_not_produce_a_duplicate_email(self):
        provider = RecordingProvider()
        deliveries = FakeDeliveries()
        service = build_service(provider=provider, deliveries=deliveries)
        report = await service.generate_and_store(
            org_id=ORG_A, analysis_id=ANALYSIS_A, context=make_context()
        )
        common = {
            "org_id": ORG_A,
            "analysis_id": ANALYSIS_A,
            "report": report,
            "recipient_email": "alice@example.com",
            "recipient_user_id": "u1",
            "document_title": "Acme Cloud EULA",
            "analysis": make_context().analysis,
            "severity_counts": {},
        }
        first = await service.send_report_email(**common)
        second = await service.send_report_email(**common)  # the worker retries

        assert first.email_sent is True
        assert second.email_sent is False
        assert second.skipped_reason == "already_delivered_or_in_flight"
        assert len(provider.sent) == 1, "exactly one email must reach the provider"

    @pytest.mark.asyncio
    async def test_pdf_is_attached_when_small_enough(self):
        provider = RecordingProvider()
        service = build_service(provider=provider)
        report = await service.generate_and_store(
            org_id=ORG_A, analysis_id=ANALYSIS_A, context=make_context()
        )
        await service.send_report_email(
            org_id=ORG_A,
            analysis_id=ANALYSIS_A,
            report=report,
            recipient_email="alice@example.com",
            recipient_user_id="u1",
            document_title="Acme",
            analysis=make_context().analysis,
            severity_counts={},
        )
        attachments = provider.sent[0].attachments
        assert attachments and attachments[0].content.startswith(b"%PDF-")
        assert attachments[0].filename.endswith(".pdf")

    @pytest.mark.asyncio
    async def test_oversized_report_falls_back_to_a_link(self):
        provider = RecordingProvider()
        service = build_service(provider=provider, max_attachment_bytes=10)
        report = await service.generate_and_store(
            org_id=ORG_A, analysis_id=ANALYSIS_A, context=make_context()
        )
        await service.send_report_email(
            org_id=ORG_A,
            analysis_id=ANALYSIS_A,
            report=report,
            recipient_email="alice@example.com",
            recipient_user_id="u1",
            document_title="Acme",
            analysis=make_context().analysis,
            severity_counts={},
        )
        message = provider.sent[0]
        assert not message.attachments
        assert "storage.example" in message.text_body

    @pytest.mark.asyncio
    async def test_transient_failure_is_retried_then_succeeds(self, monkeypatch):
        import app.services.report_delivery as module

        monkeypatch.setattr(module, "RETRY_BASE_SECONDS", 0)
        provider = RecordingProvider(fail_times=2)
        service = build_service(provider=provider, max_attempts=3)
        report = await service.generate_and_store(
            org_id=ORG_A, analysis_id=ANALYSIS_A, context=make_context()
        )
        outcome = await service.send_report_email(
            org_id=ORG_A,
            analysis_id=ANALYSIS_A,
            report=report,
            recipient_email="alice@example.com",
            recipient_user_id="u1",
            document_title="Acme",
            analysis=make_context().analysis,
            severity_counts={},
        )
        assert outcome.email_sent is True
        assert provider.attempts == 3

    @pytest.mark.asyncio
    async def test_permanent_failure_is_recorded_after_retries(self, monkeypatch):
        import app.services.report_delivery as module

        monkeypatch.setattr(module, "RETRY_BASE_SECONDS", 0)
        deliveries = FakeDeliveries()
        service = build_service(
            provider=RecordingProvider(fail_times=99), deliveries=deliveries, max_attempts=3
        )
        report = await service.generate_and_store(
            org_id=ORG_A, analysis_id=ANALYSIS_A, context=make_context()
        )
        outcome = await service.send_report_email(
            org_id=ORG_A,
            analysis_id=ANALYSIS_A,
            report=report,
            recipient_email="alice@example.com",
            recipient_user_id="u1",
            document_title="Acme",
            analysis=make_context().analysis,
            severity_counts={},
        )
        assert outcome.email_sent is False
        assert deliveries.rows[-1]["status"] == "permanently_failed"

    @pytest.mark.asyncio
    async def test_email_failure_does_not_touch_analysis_state(self, monkeypatch):
        """The delivery service must not be able to write to `analyses` at all."""
        import inspect

        import app.services.report_delivery as module

        source = inspect.getsource(module)
        assert "analyses_repo.complete" not in source
        assert "analyses.fail" not in source
        assert "UPDATE analyses" not in source

    @pytest.mark.asyncio
    async def test_storage_failure_still_sends_a_link(self):
        provider = RecordingProvider()
        service = build_service(provider=provider, storage=FakeStorage(fail_download=True))
        report = await service.generate_and_store(
            org_id=ORG_A, analysis_id=ANALYSIS_A, context=make_context()
        )
        outcome = await service.send_report_email(
            org_id=ORG_A,
            analysis_id=ANALYSIS_A,
            report=report,
            recipient_email="alice@example.com",
            recipient_user_id="u1",
            document_title="Acme",
            analysis=make_context().analysis,
            severity_counts={},
        )
        assert outcome.email_sent is True
        assert not provider.sent[0].attachments


class TestEmailContent:
    def test_body_states_completion_score_and_counts(self):
        text, html_body = build_email_bodies(
            document_title="Acme EULA",
            analysis={"status": "complete", "overall_score": 68.0, "risk_band": "elevated"},
            severity_counts={"critical": 1, "high": 2},
            download_url=None,
            attached=True,
        )
        for body in (text, html_body):
            assert "Acme EULA" in body
            assert "68.0" in body
            assert "elevated" in body
            assert "critical: 1" in body
            assert "Not legal advice" in body or "not legal advice" in body.lower()

    def test_partial_status_is_stated(self):
        text, _ = build_email_bodies(
            document_title="D",
            analysis={"status": "partial", "overall_score": 10, "risk_band": "low"},
            severity_counts={},
            download_url=None,
            attached=True,
        )
        assert "partially" in text

    def test_html_body_escapes_injected_markup(self):
        _, html_body = build_email_bodies(
            document_title="<script>alert(1)</script>",
            analysis={"status": "complete", "overall_score": 1, "risk_band": "low"},
            severity_counts={},
            download_url="https://x/y",
            attached=False,
        )
        assert "<script>" not in html_body
        assert "&lt;script&gt;" in html_body

    def test_link_mode_mentions_expiry(self):
        text, _ = build_email_bodies(
            document_title="D",
            analysis={"status": "complete", "overall_score": 1, "risk_band": "low"},
            severity_counts={},
            download_url="https://x/y",
            attached=False,
        )
        assert "expires" in text

    def test_severity_counts_ignore_unverified_findings(self):
        counts = severity_counts_from(
            [
                {"verification_status": "verified", "effective_severity": "high"},
                {"verification_status": "quarantined", "effective_severity": "critical"},
                {"verification_status": "needs_review", "effective_severity": "critical"},
            ]
        )
        assert counts == {"high": 1}, "quarantined findings must not be counted"


class TestProviderSelection:
    def test_console_is_the_safe_default(self):
        from app.core.config import Settings

        provider = build_email_provider(Settings(_env_file=None, environment="test"))
        assert isinstance(provider, ConsoleEmailProvider)

    @pytest.mark.asyncio
    async def test_console_provider_sends_nothing_but_reports_success(self):
        result = await ConsoleEmailProvider().send(
            EmailMessage(to="a@b.com", subject="s", text_body="t")
        )
        assert result.ok is True
        assert result.provider == "console"

    def test_smtp_requires_a_host(self):
        from app.providers.email.providers import SmtpEmailProvider

        with pytest.raises(ValueError, match="SMTP_HOST"):
            SmtpEmailProvider(host="", port=587, username=None, password=None, sender="a@b.com")

    def test_resend_requires_an_api_key(self):
        from app.providers.email.providers import ResendEmailProvider

        with pytest.raises(ValueError, match="RESEND_API_KEY"):
            ResendEmailProvider(api_key="", sender="a@b.com")

    def test_attachment_dataclass_defaults_to_pdf(self):
        assert Attachment(filename="a.pdf", content=b"x").content_type == "application/pdf"
