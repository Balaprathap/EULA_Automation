# Security model

ClauseGuard handles other people's contracts. That data is confidential by
definition, so the design assumes that any single control may fail and layers
accordingly.

## Threat model

| Threat | Control |
|---|---|
| One tenant reads another tenant's documents | RLS at the database, explicit org scoping in every API query, route guards in the UI |
| A malicious document manipulates the analysis | Untrusted-data delimiters, no severity field in the schema, weights never in prompts, tools bound to one document, deterministic scoring |
| The model fabricates evidence | Every quote verified against the stored chunk before persistence; failures quarantined |
| A stolen or forged token | Signature, expiry, and audience verified server-side; org resolved from the database, not from claims |
| Secrets leaked through logs | Recursive redaction by key name and token pattern; document text and quotes never logged |
| Malicious upload (zip bomb, renamed binary, oversized file) | Magic-byte sniffing, decompression-ratio guard, size and page budgets |
| Abuse or runaway cost | Per-organization sliding-window rate limits on requests and analyses |
| Untraceable actions | Append-only audit log with actor, action, resource, request id, and timestamp |

## Three-layer authorization

Data isolation is enforced independently at three levels. A bug in one is not
sufficient to leak data.

### 1. Database — Row-Level Security

Migration `0008_row_level_security.sql` enables RLS on all 13 tenant tables and
`FORCE`s it on `documents`, `document_chunks`, `findings`, and
`finding_evidence`, so even a table owner cannot read across tenants.

Every policy routes through one helper:

```sql
CREATE FUNCTION auth_org_id() RETURNS UUID
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp        -- prevents search_path shadowing
AS $$ SELECT org_id FROM profiles WHERE id = auth.uid() $$;
```

Tenancy is therefore defined in exactly one place. `tests/test_migrations.py`
asserts that **every** policy in the migration references `auth_org_id()`, that
every tenant table has a `SELECT` policy, and that every `SECURITY DEFINER`
function pins its `search_path`.

Child tables re-check the parent relationship, not just their own `org_id`:

```sql
CREATE POLICY chunks_select ON document_chunks FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM documents d
                WHERE d.id = document_chunks.document_id
                  AND d.org_id = auth_org_id()
                  AND d.deleted_at IS NULL)
);
```

`finding_reviews` has `SELECT` and `INSERT` policies and deliberately **no**
`UPDATE` or `DELETE` policy, making the review history append-only at the
database level.

The backend service role bypasses RLS by necessity — the worker must write
across the schema. RLS is therefore the backstop for direct client access via
Supabase, and layer 2 is what protects the API surface.

### 2. API — explicit ownership checks

Every authenticated request resolves the organization server-side:

```python
claims  = decode_supabase_jwt(token, settings.supabase_jwt_secret)
profile = await fetch_one("SELECT id, org_id, email, role FROM profiles WHERE id = $1",
                          claims["sub"])
```

The org comes from the **database**, keyed by the verified JWT subject — never
from a header, query parameter, or a claim the user could set. Every repository
query then takes `org_id` as a bind parameter.

Cross-tenant reads return `404` with a message **identical** to a genuinely
missing resource, so the response cannot be used to probe for existence.
`tests/test_api.py::TestTenantIsolation` asserts this equivalence directly, and
verifies isolation in both directions.

Role checks are a separate dependency:

```python
async def require_admin(user = Depends(get_current_user)):
    if not user.is_admin:
        raise Forbidden("This action requires an administrator or owner role.",
                        code="ADMIN_REQUIRED")
    return user
```

### 3. Frontend — route guards

`RequireAuth` redirects unauthenticated users. This shapes navigation only;
removing it would change what the UI shows, not what data a user can reach. The
admin page renders an explicit "the API refused this request" state rather than
hiding the failure.

## Authentication

Supabase Auth issues HS256 JWTs. The API verifies signature, expiry, and
audience itself using `SUPABASE_JWT_SECRET`, requiring both `exp` and `sub`
claims. Rejection messages are deliberately non-specific — a precise reason
tells an attacker which part of a forged token to fix.

A database trigger provisions an organization and an owner profile inside the
signup transaction, so a valid session always has exactly one tenant.

## Secrets

| Secret | Where it lives | Exposure |
|---|---|---|
| `SUPABASE_ANON_KEY` | Browser and server | Public by design; RLS constrains it |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend only | **Never** in browser code. Bypasses RLS |
| `SUPABASE_JWT_SECRET` | Backend only | Verifies tokens |
| `ANTHROPIC_API_KEY` | Backend only | Never reaches the browser |
| `EMBEDDING_API_KEY` | Backend only | Never reaches the browser |
| `DATABASE_URL` | Backend only | Full database credentials |

Only `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, and
`NEXT_PUBLIC_API_BASE_URL` are ever exposed to the browser. `.env` is
gitignored; CI fails the build if a `.env` file is committed, and runs
`gitleaks` on every pull request.

## Logging and redaction

`app/core/logging.py` emits structured JSON with a correlation id, and redacts
recursively at any nesting depth:

- **By key name** — `password`, `token`, `access_token`, `refresh_token`, `jwt`,
  `authorization`, `api_key`, `anthropic_api_key`, `supabase_service_role_key`,
  `cookie`, and content fields `text`, `normalized_text`, `chunk_text`, `quote`,
  `evidence`, `content`.
- **By pattern** — `Bearer <token>`, JWT-shaped strings, `sk-*`, `sk-ant-*`.
- **By length** — strings truncate at 200 characters.

**Never logged:** full document text, complete evidence quotes, passwords, API
keys, JWTs, refresh tokens, authorization headers, the service-role key.

Sentry, when configured, runs with `send_default_pii=False`.

## Upload validation

Order matters — cheap checks run first:

1. Size limit (default 10 MB) before any parsing.
2. Magic-byte sniffing. The extension is a *secondary* signal only, so a
   renamed executable is rejected. A ZIP without `word/document.xml` is not a
   DOCX.
3. Encrypted PDFs rejected (an empty user password is attempted first).
4. Page budget (default 150).
5. DOCX decompression-ratio guard (200:1, 400 MB absolute) against zip bombs.
6. **Scanned-PDF detection by text density** — fewer than 120 characters per
   page, or under 200 characters total, is rejected with an actionable message
   rather than analyzed as an empty document.

Files are stored in a **private** Supabase bucket under `{org_id}/{document_id}/`.
The storage RLS policy checks `(storage.foldername(name))[1] = auth_org_id()::text`.
No public URL is ever created; downloads use 5-minute signed URLs minted only
after an ownership check.

## Transport and browser headers

Applied to every API response:

- `Content-Security-Policy` — `default-src 'self'`, `frame-ancestors 'none'`,
  `object-src 'none'`, explicit `connect-src` allowlist
- `Strict-Transport-Security` — production only, `max-age=31536000; includeSubDomains; preload`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy` — geolocation, microphone, camera, payment all denied
- `Cross-Origin-Opener-Policy: same-origin`

CORS uses an explicit allowlist. **Production startup fails** if `CORS_ORIGINS`
contains `*`, asserted by `tests/test_api.py::TestCorsConfiguration`.

### Interactive documentation and CSP

Swagger UI and ReDoc are loaded from a CDN by FastAPI's default templates, and
the Swagger template emits an inline initialiser. Both are blocked by the strict
application policy (`script-src 'self'`), which renders `/docs` as a blank page.

`/docs`, `/redoc`, `/openapi.json`, and `/docs/oauth2-redirect` therefore receive
a **documentation-specific** CSP that permits `cdn.jsdelivr.net`, Google Fonts,
`blob:` workers, and `'unsafe-inline'` scripts — and **only outside production**.
`frame-ancestors`, `base-uri`, `form-action`, and `object-src` stay identical to
the application policy on those routes, and every other security header is
unchanged.

In production these routes keep the strict policy, so the documentation UI will
not render there. That is deliberate: a public deployment should either disable
the docs routes or self-host the Swagger assets rather than allow inline scripts
from a third-party CDN. Enforced by
`tests/test_api.py::TestDocsCspIsDevelopmentOnly`.

Separately, unhandled exceptions are converted to a 500 response *inside* the
header middleware. Starlette's `ServerErrorMiddleware` sits outside user
middleware, so a re-raised exception would return a response carrying no
security headers at all.

## Rate limiting

Redis sliding window, per organization:

| Limit | Default | Variable |
|---|---:|---|
| Analyses | 20/hour | `RATE_LIMIT_ANALYSES_PER_HOUR` |
| API requests | 200/minute | `RATE_LIMIT_REQUESTS_PER_MINUTE` |

Returns `429` with `Retry-After` and a retry hint in the error details. The
limiter **fails open** when Redis is unreachable — an outage in the limiter must
not take down the API — and logs the degradation.

## Audit logging

Append-only, recording organization, actor id and email, action, resource type
and id, request id, IP, user agent, timestamp, and safe metadata. Metadata is
passed through `safe_metadata()`, which strips sensitive keys and caps value
sizes independently of the logging redactor.

Audited actions: document upload, update, delete; analysis create and complete;
policy create, update, version, rules replace; finding accept, dismiss,
escalate, override severity, note.

An audit write failure is logged but never breaks the request.

## Reporting a vulnerability

Do not open a public issue. Contact the maintainer directly with the affected
component, reproduction steps, and impact.


## AWS integration (optional, feature-flagged)

**AWS S3 and SES integrations are implemented behind feature flags and validated with botocore Stubber. Live AWS verification is pending.**

| Control | Implementation |
|---|---|
| Bucket exposure | Private, Block Public Access on, ACLs disabled. No public URL is ever produced |
| Encryption at rest | Always requested — SSE-KMS when `AWS_KMS_KEY_ID` is set, otherwise SSE-S3 |
| Download links | SigV4 pre-signed URLs, TTL clamped to 60s–1h in code, never persisted and never logged (only the TTL is logged) |
| Tenant isolation | Object keys are `{org_id}/{analysis_id}/report-v{n}.pdf`; the API checks organization ownership before issuing any URL |
| Object metadata | Only `analysis-id`, `org-id`, `report-version`, `checksum-sha256`, `generated-at`. No document title, vendor, filename, email or evidence text — asserted by test |
| Credentials | Standard boto3 credential chain (IAM role preferred). No AWS secret is read from application config or reaches the frontend |
| IAM | Least privilege: `PutObject`/`GetObject`/`DeleteObject` scoped to the bucket ARN, `ses:SendEmail` conditioned on the From address. No `s3:*` |
| Recipient control | SES cannot choose a recipient; the address is resolved server-side from the authenticated profile, as with every other provider |
| Failure isolation | No AWS code path writes to `analyses`, `findings` or scores — asserted by test. The worker hook stays wrapped |

### Residual risk

- **Not yet verified against real AWS.** botocore's `Stubber` validates request
  shapes offline; it does not exercise IAM, bucket policy, or SES sandbox state.
- A pre-signed URL is a bearer credential for its lifetime. TTL defaults to 15
  minutes and is capped at 1 hour.
- SES bounce and complaint handling is not implemented; it is required before
  requesting production access.

## Google Cloud

No Google Cloud integration exists. No Vertex AI, Gemini, BigQuery, Cloud Run or
service-account credential is present in this repository, and no confidential
content is sent to Google.
