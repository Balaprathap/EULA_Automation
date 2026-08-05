"""API-surface tests: authentication, authorization, tenant isolation, the
error envelope, and rate limiting.

Repositories are replaced with in-memory fakes so the whole suite runs without
Postgres or Redis. The authorization logic under test is the real dependency
chain from app/api/deps.py.
"""

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("ENVIRONMENT", "test")

from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_current_user, require_admin  # noqa: E402
from app.core.errors import Forbidden  # noqa: E402
from app.core.security import AuthenticatedUser  # noqa: E402

ORG_A = "aaaaaaaa-0000-0000-0000-000000000001"
ORG_B = "bbbbbbbb-0000-0000-0000-000000000002"

NOW = datetime.now(timezone.utc)

USER_A = AuthenticatedUser(user_id="user-a", email="a@example.com", org_id=ORG_A, role="member")
ADMIN_A = AuthenticatedUser(
    user_id="admin-a", email="admin@example.com", org_id=ORG_A, role="admin"
)
OWNER_A = AuthenticatedUser(
    user_id="owner-a", email="owner@example.com", org_id=ORG_A, role="owner"
)
USER_B = AuthenticatedUser(user_id="user-b", email="b@example.com", org_id=ORG_B, role="owner")


def document_row(doc_id: str, org_id: str, **overrides):
    row = {
        "id": doc_id,
        "org_id": org_id,
        "title": "Acme EULA",
        "vendor_name": "Acme",
        "source_type": "txt",
        "original_filename": "acme.txt",
        "file_size_bytes": 1234,
        "page_count": 3,
        "char_count": 5000,
        "status": "ready",
        "error_code": None,
        "error_message": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


class FakeDocumentRepository:
    """In-memory store that enforces org scoping exactly as the SQL does."""

    def __init__(self):
        self.rows = {
            "doc-a": document_row("doc-a", ORG_A),
            "doc-b": document_row("doc-b", ORG_B, title="Other tenant's contract"),
        }
        self.deleted = set()

    async def get(self, org_id, document_id, with_text=False):
        row = self.rows.get(document_id)
        if row is None or row["org_id"] != org_id or document_id in self.deleted:
            return None
        return dict(row)

    async def list(self, org_id, **kwargs):
        items = [
            dict(r)
            for r in self.rows.values()
            if r["org_id"] == org_id and r["id"] not in self.deleted
        ]
        return {"items": items, "total": len(items), "limit": 25, "offset": 0}

    async def get_normalized_text(self, org_id, document_id):
        row = await self.get(org_id, document_id)
        return "The vendor may retain data indefinitely." if row else None

    async def update_metadata(self, org_id, document_id, **fields):
        row = await self.get(org_id, document_id)
        if row is None:
            return None
        for key, value in fields.items():
            if value is not None:
                self.rows[document_id][key] = value
        return dict(self.rows[document_id])

    async def soft_delete(self, org_id, document_id):
        if await self.get(org_id, document_id) is None:
            return False
        self.deleted.add(document_id)
        return True


@pytest.fixture
def client(monkeypatch):
    import app.api.v1.documents as documents_module
    import app.api.v1.findings as findings_module
    from app.main import app

    fake_documents = FakeDocumentRepository()
    monkeypatch.setattr(documents_module, "documents", fake_documents)
    monkeypatch.setattr(findings_module, "documents", fake_documents)

    async def no_audit(**_kwargs):
        return None

    monkeypatch.setattr(documents_module, "record_audit", no_audit)
    monkeypatch.setattr(findings_module, "record_audit", no_audit)

    app.dependency_overrides[get_current_user] = lambda: USER_A
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.fake_documents = fake_documents
        yield test_client
    app.dependency_overrides.clear()


def as_user(client, user):
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    return client


class TestHealth:
    def test_liveness_is_public(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_root_states_the_project_topic(self, client):
        body = client.get("/").json()
        assert body["topic"] == "Automated EULA Compliance Extraction"
        assert body["name"] == "ClauseGuard"
        assert "not legal advice" in body["disclaimer"]

    def test_openapi_schema_is_served(self, client):
        schema = client.get("/openapi.json").json()
        assert schema["info"]["title"] == "ClauseGuard API"
        assert "/api/v1/documents" in schema["paths"]


class TestAuthenticationRequired:
    """Without the dependency override, every business endpoint must reject."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/api/v1/documents"),
            ("get", "/api/v1/documents/doc-a"),
            ("delete", "/api/v1/documents/doc-a"),
            ("get", "/api/v1/policies"),
            ("get", "/api/v1/analyses"),
        ],
    )
    def test_missing_token_is_rejected(self, method, path):
        from app.main import app

        app.dependency_overrides.clear()
        with TestClient(app, raise_server_exceptions=False) as anonymous:
            response = getattr(anonymous, method)(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_malformed_authorization_header_is_rejected(self):
        from app.main import app

        app.dependency_overrides.clear()
        with TestClient(app, raise_server_exceptions=False) as anonymous:
            response = anonymous.get("/api/v1/documents", headers={"Authorization": "Basic abc123"})
        assert response.status_code == 401


class TestTenantIsolation:
    """Organization A must never see or touch organization B's data."""

    def test_listing_shows_only_your_own_documents(self, client):
        titles = [d["title"] for d in client.get("/api/v1/documents").json()["items"]]
        assert titles == ["Acme EULA"]
        assert "Other tenant's contract" not in titles

    def test_reading_another_orgs_document_is_404(self, client):
        response = client.get("/api/v1/documents/doc-b")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    def test_the_404_does_not_confirm_existence(self, client):
        existing_other_org = client.get("/api/v1/documents/doc-b")
        nonexistent = client.get("/api/v1/documents/doc-zzz")
        assert existing_other_org.status_code == nonexistent.status_code
        assert (
            existing_other_org.json()["error"]["message"] == nonexistent.json()["error"]["message"]
        )

    def test_modifying_another_orgs_document_is_404(self, client):
        response = client.patch("/api/v1/documents/doc-b", json={"title": "Hijacked"})
        assert response.status_code == 404
        assert client.fake_documents.rows["doc-b"]["title"] == "Other tenant's contract"

    def test_deleting_another_orgs_document_is_404(self, client):
        assert client.delete("/api/v1/documents/doc-b").status_code == 404
        assert "doc-b" not in client.fake_documents.deleted

    def test_reading_another_orgs_document_text_is_404(self, client):
        assert client.get("/api/v1/documents/doc-b/text").status_code == 404

    def test_the_other_tenant_sees_the_mirror_image(self, client):
        as_user(client, USER_B)
        assert client.get("/api/v1/documents/doc-b").status_code == 200
        assert client.get("/api/v1/documents/doc-a").status_code == 404


class TestOwnDataAccess:
    def test_reading_your_own_document_succeeds(self, client):
        body = client.get("/api/v1/documents/doc-a").json()
        assert body["id"] == "doc-a"
        assert body["title"] == "Acme EULA"

    def test_updating_your_own_document_succeeds(self, client):
        response = client.patch("/api/v1/documents/doc-a", json={"title": "Renamed"})
        assert response.status_code == 200
        assert response.json()["title"] == "Renamed"

    def test_deleting_your_own_document_succeeds(self, client):
        assert client.delete("/api/v1/documents/doc-a").status_code == 204
        assert client.get("/api/v1/documents/doc-a").status_code == 404

    def test_document_text_endpoint_returns_normalized_text(self, client):
        body = client.get("/api/v1/documents/doc-a/text").json()
        assert body["char_count"] == len(body["normalized_text"])


class TestRoleAuthorization:
    """Policy administration is admin-only, enforced in the API, not just the UI."""

    @pytest.mark.asyncio
    async def test_member_is_refused_admin_scope(self):
        with pytest.raises(Forbidden) as excinfo:
            await require_admin(USER_A)
        assert excinfo.value.code == "ADMIN_REQUIRED"

    @pytest.mark.asyncio
    async def test_admin_is_allowed(self):
        assert (await require_admin(ADMIN_A)).is_admin

    @pytest.mark.asyncio
    async def test_owner_is_allowed(self):
        assert (await require_admin(OWNER_A)).is_admin

    def test_role_helpers(self):
        assert USER_A.is_admin is False
        assert ADMIN_A.is_admin is True and ADMIN_A.is_owner is False
        assert OWNER_A.is_owner is True


class TestErrorEnvelope:
    def test_every_error_has_code_message_and_request_id(self, client):
        error = client.get("/api/v1/documents/doc-b").json()["error"]
        assert set(error) >= {"code", "message", "request_id"}
        assert error["request_id"]

    def test_request_id_header_matches_the_body(self, client):
        response = client.get("/api/v1/documents/doc-b")
        assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]

    def test_a_supplied_request_id_is_echoed(self, client):
        response = client.get("/api/v1/documents/doc-a", headers={"X-Request-ID": "trace-42"})
        assert response.headers["X-Request-ID"] == "trace-42"

    def test_validation_failures_list_the_offending_fields(self, client):
        response = client.patch("/api/v1/documents/doc-a", json={"title": ""})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_REQUEST"
        assert response.json()["error"]["details"]["fields"]

    def test_unknown_route_uses_the_same_envelope(self, client):
        body = client.get("/api/v1/nope").json()
        assert body["error"]["code"] == "NOT_FOUND"


class TestSecurityHeaders:
    @pytest.mark.parametrize(
        "header",
        [
            "Content-Security-Policy",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Permissions-Policy",
        ],
    )
    def test_header_is_present(self, client, header):
        assert header in client.get("/health").headers

    def test_framing_is_denied(self, client):
        headers = client.get("/health").headers
        assert headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]

    def test_mime_sniffing_is_disabled(self, client):
        assert client.get("/health").headers["X-Content-Type-Options"] == "nosniff"


def parse_csp(value: str) -> dict:
    directives = {}
    for part in value.split(";"):
        bits = part.strip().split()
        if bits:
            directives[bits[0]] = bits[1:]
    return directives


class TestInteractiveDocs:
    """Regression: /docs returned 200 but rendered a blank page.

    FastAPI's Swagger UI template loads swagger-ui-bundle.js from jsDelivr and
    emits an inline initialiser, both of which the strict application CSP
    (`script-src 'self'`) blocked. The documentation routes now get their own
    policy outside production.
    """

    def test_docs_returns_200(self, client):
        assert client.get("/docs").status_code == 200

    def test_redoc_returns_200(self, client):
        assert client.get("/redoc").status_code == 200

    def test_openapi_json_returns_200(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        assert response.json()["info"]["title"] == "ClauseGuard API"

    def test_docs_page_is_not_empty(self, client):
        body = client.get("/docs").text
        assert "swagger-ui" in body
        assert "/openapi.json" in body

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_docs_csp_permits_the_cdn(self, client, path):
        directives = parse_csp(client.get(path).headers["content-security-policy"])
        assert "https://cdn.jsdelivr.net" in directives["script-src"]
        assert "https://cdn.jsdelivr.net" in directives["style-src"]

    def test_docs_csp_permits_the_inline_initialiser(self, client):
        directives = parse_csp(client.get("/docs").headers["content-security-policy"])
        assert "'unsafe-inline'" in directives["script-src"]

    def test_every_swagger_asset_is_permitted(self, client):
        """Parse the real HTML and check each referenced asset against the CSP."""
        import re

        response = client.get("/docs")
        directives = parse_csp(response.headers["content-security-policy"])
        html = response.text

        def permitted(key, url):
            sources = directives.get(key, [])
            return any(url.startswith(s) for s in sources if s.startswith("http"))

        scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
        styles = re.findall(r'<link[^>]+rel="stylesheet"[^>]*href="([^"]+)"', html)
        assert scripts, "expected Swagger UI to reference an external bundle"
        for url in scripts:
            assert permitted("script-src", url), f"script blocked by CSP: {url}"
        for url in styles:
            assert permitted("style-src", url), f"stylesheet blocked by CSP: {url}"

    def test_redoc_needs_blob_workers_and_google_fonts(self, client):
        directives = parse_csp(client.get("/redoc").headers["content-security-policy"])
        assert "blob:" in directives["worker-src"]
        assert "https://fonts.gstatic.com" in directives["font-src"]

    @pytest.mark.parametrize(
        "header",
        [
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Permissions-Policy",
            "Cross-Origin-Opener-Policy",
        ],
    )
    def test_all_other_security_headers_remain_on_docs(self, client, header):
        assert header in client.get("/docs").headers

    @pytest.mark.parametrize(
        "directive", ["frame-ancestors", "base-uri", "form-action", "object-src"]
    )
    def test_docs_csp_keeps_the_non_rendering_protections_strict(self, client, directive):
        docs = parse_csp(client.get("/docs").headers["content-security-policy"])
        api = parse_csp(client.get("/health").headers["content-security-policy"])
        assert docs[directive] == api[directive]


class TestApplicationCspIsUnchanged:
    """The relaxation must not leak onto any normal route."""

    @pytest.mark.parametrize("path", ["/health", "/", "/api/v1/documents", "/api/v1/policies"])
    def test_normal_routes_keep_the_strict_policy(self, client, path):
        directives = parse_csp(client.get(path).headers["content-security-policy"])
        assert directives["script-src"] == ["'self'"]
        assert "'unsafe-inline'" not in directives["script-src"]
        assert not any("jsdelivr" in s for s in directives["script-src"])

    def test_no_cdn_anywhere_in_the_application_policy(self, client):
        csp = client.get("/health").headers["content-security-policy"]
        for origin in ("cdn.jsdelivr.net", "fonts.googleapis.com", "fastapi.tiangolo.com"):
            assert origin not in csp

    def test_error_responses_still_carry_security_headers(self, client):
        """Regression: an unhandled exception used to bypass this middleware
        entirely, because Starlette's ServerErrorMiddleware sits outside user
        middleware - so 500 responses shipped with no CSP at all."""
        # /api/v1/policies hits an unmocked repository and raises.
        response = client.get("/api/v1/policies")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert response.json()["error"]["request_id"]
        assert "content-security-policy" in response.headers
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_error_response_leaks_no_internal_detail(self, client):
        body = client.get("/api/v1/policies").text
        for leak in ("Traceback", "asyncpg", "pool has not been initialised", "app/db"):
            assert leak not in body

    def test_docs_and_application_policies_actually_differ(self, client):
        assert (
            client.get("/docs").headers["content-security-policy"]
            != client.get("/health").headers["content-security-policy"]
        )


class TestDocsCspIsDevelopmentOnly:
    """Production keeps the strict policy on every route, /docs included."""

    from app.core.security import security_headers as _sh

    def test_production_docs_get_the_strict_policy(self):
        from app.core.security import application_csp, security_headers

        headers = security_headers(
            "https://app.example.com", "https://api.example.com", production=True, path="/docs"
        )
        assert headers["Content-Security-Policy"] == application_csp(
            "https://app.example.com", "https://api.example.com"
        )
        assert "jsdelivr" not in headers["Content-Security-Policy"]

    def test_development_docs_get_the_relaxed_policy(self):
        from app.core.security import docs_csp, security_headers

        headers = security_headers(
            "http://localhost:3000", "http://localhost:8000", production=False, path="/docs"
        )
        assert headers["Content-Security-Policy"] == docs_csp(
            "http://localhost:3000", "http://localhost:8000"
        )

    @pytest.mark.parametrize("path", ["/health", "/api/v1/documents", "/"])
    def test_non_docs_paths_are_never_relaxed(self, path):
        from app.core.security import security_headers

        for production in (True, False):
            csp = security_headers(
                "http://localhost:3000",
                "http://localhost:8000",
                production=production,
                path=path,
            )["Content-Security-Policy"]
            assert "jsdelivr" not in csp
            assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]

    def test_hsts_still_only_in_production(self):
        from app.core.security import security_headers

        prod = security_headers("a", "b", production=True, path="/docs")
        dev = security_headers("a", "b", production=False, path="/docs")
        assert "Strict-Transport-Security" in prod
        assert "Strict-Transport-Security" not in dev

    def test_docs_path_detection(self):
        from app.core.security import is_docs_path

        for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            assert is_docs_path(path), path
        for path in ("/health", "/api/v1/documents", "/docsomething", "/api/v1/docs"):
            assert not is_docs_path(path), path


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_requests_are_allowed_under_the_limit(self):
        from app.core.ratelimit import RateLimiter

        limiter = RateLimiter(redis_client=None)
        decision = await limiter.check(key="org", limit=10, window_seconds=60)
        assert decision.allowed

    @pytest.mark.asyncio
    async def test_the_limiter_fails_open_when_redis_breaks(self):
        from app.core.ratelimit import RateLimiter

        class BrokenRedis:
            def pipeline(self):
                raise ConnectionError("redis is down")

        decision = await RateLimiter(BrokenRedis()).check(key="org", limit=5, window_seconds=60)
        assert decision.allowed, "a limiter outage must not take down the API"

    def test_rate_limit_errors_carry_retry_after(self):
        from app.core.errors import RateLimited

        error = RateLimited("slow down", retry_after=42)
        assert error.status_code == 429
        assert error.envelope("req-1")["error"]["details"]["retry_after_seconds"] == 42


class TestCorsConfiguration:
    def test_production_rejects_a_wildcard_origin(self, monkeypatch):
        from app.core.config import Settings

        with pytest.raises(ValueError, match="allowlist"):
            Settings(
                _env_file=None,  # never read the developer's real .env
                environment="production",
                cors_origins=["*"],
                database_url="postgresql://x",
                supabase_url="https://x.supabase.co",
                supabase_service_role_key="k",
                supabase_jwt_secret="s",
                anthropic_api_key="a",
                embedding_api_key="e",
            )

    def test_production_rejects_the_test_embedding_provider(self):
        from app.core.config import Settings

        with pytest.raises(ValueError, match="test-only"):
            Settings(
                _env_file=None,
                environment="production",
                embedding_provider="deterministic",
                cors_origins=["https://app.example.com"],
                database_url="postgresql://x",
                supabase_url="https://x.supabase.co",
                supabase_service_role_key="k",
                supabase_jwt_secret="s",
                anthropic_api_key="a",
            )

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # Regression: the plain comma-separated form documented in
            # .env.example used to crash startup, because pydantic-settings
            # JSON-decoded the list before the validator ran.
            ("http://localhost:3000", ["http://localhost:3000"]),
            ("http://a.com,http://b.com", ["http://a.com", "http://b.com"]),
            ("http://a.com , http://b.com ,", ["http://a.com", "http://b.com"]),
            ('["http://localhost:3000"]', ["http://localhost:3000"]),
            ('["http://a.com", "http://b.com"]', ["http://a.com", "http://b.com"]),
        ],
    )
    def test_cors_origins_accepts_both_formats(self, raw, expected, monkeypatch):
        from app.core.config import Settings

        monkeypatch.setenv("CORS_ORIGINS", raw)
        assert Settings(_env_file=None).cors_origins == expected

    def test_malformed_cors_json_gives_an_actionable_error(self, monkeypatch):
        from app.core.config import Settings

        monkeypatch.setenv("CORS_ORIGINS", '["http://a.com"')
        with pytest.raises(ValueError, match="CORS_ORIGINS"):
            Settings(_env_file=None)

    def test_missing_variables_are_named_explicitly(self):
        from app.core.config import Settings

        with pytest.raises(ValueError) as excinfo:
            Settings(
                _env_file=None,
                environment="production",
                cors_origins=["https://app.example.com"],
                database_url="postgresql://x",
                supabase_url="https://x.supabase.co",
                supabase_service_role_key="k",
                supabase_jwt_secret="s",
                embedding_api_key="e",
            )
        assert "ANTHROPIC_API_KEY" in str(excinfo.value)


class TestReportEndpoints:
    """Authorization and contract for the three report routes."""

    @pytest.fixture
    def report_client(self, client, monkeypatch):
        import app.api.v1.reports as reports_module

        class FakeAnalyses:
            rows = {
                "an-a": {
                    "id": "an-a",
                    "org_id": ORG_A,
                    "document_id": "doc-a",
                    "policy_id": "pol-a",
                    "status": "complete",
                },
                "an-b": {
                    "id": "an-b",
                    "org_id": ORG_B,
                    "document_id": "doc-b",
                    "policy_id": "pol-b",
                    "status": "complete",
                },
                "an-running": {
                    "id": "an-running",
                    "org_id": ORG_A,
                    "document_id": "doc-a",
                    "policy_id": "pol-a",
                    "status": "running",
                },
            }

            async def get(self, org_id, analysis_id):
                row = self.rows.get(analysis_id)
                return dict(row) if row and row["org_id"] == org_id else None

            async def list_categories(self, analysis_id):
                return []

        class FakeReports:
            async def latest_for_analysis(self, org_id, analysis_id):
                if analysis_id == "an-a":
                    return {
                        "id": "rep-a",
                        "version": 1,
                        "generation_status": "ready",
                        "storage_path": f"{ORG_A}/an-a/report-v1.pdf",
                        "file_size": 1234,
                        "generated_at": NOW,
                    }
                return None

        class FakeDeliveries:
            async def latest_for_analysis(self, org_id, analysis_id):
                if analysis_id == "an-a":
                    return {
                        "id": "d1",
                        "status": "sent",
                        "attempt_count": 1,
                        "recipient_masked": "a***@example.com",
                        "sent_at": NOW,
                        "error_message_safe": None,
                    }
                return None

        monkeypatch.setattr(reports_module, "analyses", FakeAnalyses())
        monkeypatch.setattr(reports_module, "reports", FakeReports())
        monkeypatch.setattr(reports_module, "deliveries", FakeDeliveries())

        async def no_audit(**_kwargs):
            return None

        monkeypatch.setattr(reports_module, "record_audit", no_audit)
        return client

    def test_status_returns_generation_and_delivery_state(self, report_client):
        body = report_client.get("/api/v1/analyses/an-a/report/status").json()
        assert body["report_available"] is True
        assert body["generation_status"] == "ready"
        assert body["email_status"] == "sent"
        assert body["can_resend"] is True

    def test_status_masks_the_recipient(self, report_client):
        body = report_client.get("/api/v1/analyses/an-a/report/status").json()
        assert body["email_masked_recipient"] == "a***@example.com"
        assert "@example.com" in body["email_masked_recipient"]
        # The full local part must never be returned to the browser.
        assert "alice" not in str(body)

    def test_another_orgs_report_status_is_404(self, report_client):
        assert report_client.get("/api/v1/analyses/an-b/report/status").status_code == 404

    def test_another_orgs_report_download_is_404(self, report_client):
        assert report_client.get("/api/v1/analyses/an-b/report").status_code == 404

    def test_another_orgs_resend_is_404(self, report_client):
        assert report_client.post("/api/v1/analyses/an-b/report/email").status_code == 404

    def test_missing_report_download_is_422_not_500(self, report_client):
        response = report_client.get("/api/v1/analyses/an-running/report")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "REPORT_NOT_READY"

    def test_resend_rejected_before_the_analysis_finishes(self, report_client):
        response = report_client.post("/api/v1/analyses/an-running/report/email")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "ANALYSIS_NOT_FINISHED"

    def test_report_routes_require_authentication(self):
        from app.main import app

        app.dependency_overrides.clear()
        with TestClient(app, raise_server_exceptions=False) as anonymous:
            for method, path in [
                ("get", "/api/v1/analyses/an-a/report"),
                ("get", "/api/v1/analyses/an-a/report/status"),
                ("post", "/api/v1/analyses/an-a/report/email"),
            ]:
                assert getattr(anonymous, method)(path).status_code == 401

    def test_resend_ignores_any_recipient_supplied_in_the_body(self, report_client):
        """A client-supplied address must have no effect whatsoever."""
        import inspect

        from app.api.v1 import reports as reports_module

        source = inspect.getsource(reports_module.resend_report_email)
        assert "recipient_email=user.email" in source
        assert "request.json" not in source
        # Posting a body with an address must not change behaviour.
        response = report_client.post(
            "/api/v1/analyses/an-running/report/email",
            json={"email": "attacker@evil.example", "to": "attacker@evil.example"},
        )
        assert response.status_code == 422  # rejected on analysis state, not on the body

    def test_download_sets_pdf_content_type_and_filename(self, report_client, monkeypatch):
        import app.api.v1.reports as reports_module

        class FakeStorage:
            async def download(self, key):
                return b"%PDF-1.4 fake report bytes"

        monkeypatch.setattr(reports_module, "_storage", lambda settings: FakeStorage())

        class FakeDocuments:
            async def get(self, org_id, document_id):
                return {"title": "Acme Cloud EULA"}

        monkeypatch.setattr(reports_module, "documents", FakeDocuments())

        response = report_client.get("/api/v1/analyses/an-a/report")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment;")
        assert ".pdf" in disposition
        assert response.headers["cache-control"] == "private, no-store"
        assert response.content.startswith(b"%PDF-")


class TestActionItemEndpoints:
    """Cross-tenant isolation and human-edit safety for action items."""

    @pytest.fixture
    def items_client(self, client, monkeypatch):
        import app.api.v1.action_items as module

        store = {
            "ai-a": {
                "id": "ai-a",
                "org_id": ORG_A,
                "analysis_id": "an-a",
                "document_id": "doc-a",
                "finding_id": "f-a",
                "title": "Diarise renewal",
                "description": "d",
                "category": "automatic_renewal",
                "obligation_type": "automatic_renewal",
                "evidence_quote": "ninety (90) days",
                "doc_start_offset": 1,
                "doc_end_offset": 2,
                "duration_days": 90,
                "duration_text": "(90) days",
                "ai_due_date": None,
                "ai_priority": "urgent",
                "due_date": None,
                "date_status": "unresolved",
                "assignee_id": None,
                "priority": "urgent",
                "status": "open",
                "reviewer_note": None,
                "dedupe_key": "k1",
                "completed_at": None,
                "completed_by": None,
                "created_at": NOW,
                "updated_at": NOW,
            },
            "ai-b": {
                **{"id": "ai-b", "org_id": ORG_B, "analysis_id": "an-b", "document_id": "doc-b"},
                "finding_id": "f-b",
                "title": "Other tenant",
                "description": "d",
                "category": "cancellation",
                "obligation_type": "cancellation_deadline",
                "evidence_quote": "q",
                "doc_start_offset": None,
                "doc_end_offset": None,
                "duration_days": None,
                "duration_text": None,
                "ai_due_date": None,
                "ai_priority": "high",
                "due_date": None,
                "date_status": "unresolved",
                "assignee_id": None,
                "priority": "high",
                "status": "open",
                "reviewer_note": None,
                "dedupe_key": "k2",
                "completed_at": None,
                "completed_by": None,
                "created_at": NOW,
                "updated_at": NOW,
            },
        }

        class FakeItems:
            edits: list = []

            async def get(self, org_id, item_id):
                row = store.get(item_id)
                return dict(row) if row and row["org_id"] == org_id else None

            async def list(self, org_id, **kwargs):
                rows = [dict(r) for r in store.values() if r["org_id"] == org_id]
                return {"items": rows, "total": len(rows), "limit": 50, "offset": 0}

            async def summary(self, org_id):
                return {
                    "open_count": 1,
                    "completed_count": 0,
                    "overdue_count": 0,
                    "due_soon_count": 0,
                    "urgent_count": 1,
                    "unresolved_date_count": 1,
                }

            async def update(self, *, org_id, item_id, reviewer_id, changes):
                row = store.get(item_id)
                if not row or row["org_id"] != org_id:
                    return None
                self.edits.append((item_id, dict(changes)))
                row.update(changes)
                return dict(row)

            async def list_reviews(self, org_id, item_id):
                return []

        class FakeAnalyses:
            async def get(self, org_id, analysis_id):
                if analysis_id == "an-a" and org_id == ORG_A:
                    return {
                        "id": "an-a",
                        "org_id": ORG_A,
                        "document_id": "doc-a",
                        "status": "complete",
                    }
                return None

        fake_items = FakeItems()
        monkeypatch.setattr(module, "items", fake_items)
        monkeypatch.setattr(module, "analyses", FakeAnalyses())

        async def no_audit(**_kwargs):
            return None

        monkeypatch.setattr(module, "record_audit", no_audit)
        client.fake_items = fake_items
        return client

    def test_list_returns_only_this_organizations_items(self, items_client):
        body = items_client.get("/api/v1/action-items").json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "ai-a"
        assert "Other tenant" not in str(body)

    def test_summary_powers_the_dashboard_widget(self, items_client):
        body = items_client.get("/api/v1/action-items/summary").json()
        assert body["urgent_count"] == 1
        assert body["unresolved_date_count"] == 1

    def test_cross_tenant_item_patch_is_404(self, items_client):
        response = items_client.patch("/api/v1/action-items/ai-b", json={"status": "completed"})
        assert response.status_code == 404
        assert items_client.fake_items.edits == [], "no edit may be applied cross-tenant"

    def test_cross_tenant_history_is_404(self, items_client):
        assert items_client.get("/api/v1/action-items/ai-b/history").status_code == 404

    def test_user_can_update_status(self, items_client):
        response = items_client.patch("/api/v1/action-items/ai-a", json={"status": "in_progress"})
        assert response.status_code == 200
        assert response.json()["status"] == "in_progress"

    def test_user_can_set_a_due_date_manually(self, items_client):
        response = items_client.patch("/api/v1/action-items/ai-a", json={"due_date": "2026-12-01"})
        assert response.status_code == 200
        assert response.json()["due_date"] == "2026-12-01"

    def test_machine_fields_cannot_be_overwritten(self, items_client):
        """extra='forbid' means the AI extraction is not editable via the API."""
        for field in ("evidence_quote", "duration_days", "ai_priority", "finding_id", "title"):
            response = items_client.patch("/api/v1/action-items/ai-a", json={field: "tampered"})
            assert response.status_code == 400, f"{field} must be rejected"

    def test_empty_patch_is_rejected(self, items_client):
        assert items_client.patch("/api/v1/action-items/ai-a", json={}).status_code == 400

    def test_invalid_status_is_rejected(self, items_client):
        assert (
            items_client.patch("/api/v1/action-items/ai-a", json={"status": "nonsense"}).status_code
            == 400
        )

    def test_generate_for_another_org_analysis_is_404(self, items_client):
        assert items_client.post("/api/v1/analyses/an-b/action-items/generate").status_code == 404

    def test_action_item_routes_require_authentication(self):
        from app.main import app

        app.dependency_overrides.clear()
        with TestClient(app, raise_server_exceptions=False) as anonymous:
            for method, path in [
                ("get", "/api/v1/action-items"),
                ("get", "/api/v1/action-items/summary"),
                ("patch", "/api/v1/action-items/ai-a"),
                ("post", "/api/v1/analyses/an-a/action-items/generate"),
            ]:
                assert getattr(anonymous, method)(path).status_code == 401
