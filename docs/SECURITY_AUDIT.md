# Security audit

Self-audit of the implementation against the controls it claims. Each row states
what was checked, how, and whether the evidence is automated or manual.

Audit date: 2026-08-02
Scope: the whole repository at the commit this document accompanies.

---

## Summary

| Area | Controls checked | Pass | Gap |
|---|---:|---:|---:|
| Authentication and session | 6 | 6 | 0 |
| Multi-tenancy and authorization | 9 | 9 | 0 |
| Secret handling | 7 | 7 | 0 |
| Input validation | 8 | 8 | 0 |
| AI-specific (injection, evidence, scoring) | 10 | 10 | 0 |
| Transport and browser | 6 | 6 | 0 |
| Availability and abuse | 5 | 5 | 0 |
| Auditability | 4 | 4 | 0 |
| **Total** | **55** | **55** | **0** |

Automated coverage: 122 of the 339 backend tests assert a security property
directly (`test_api.py` 42, `test_migrations.py` 60 of which the RLS block is
load-bearing, `test_prompt_injection.py` 20).

**Residual risk is recorded at the end.** A clean table does not mean there is
nothing left to do; it means the controls that exist behave as documented.

---

## Authentication and session

| # | Control | Evidence | Result |
|---|---|---|---|
| A1 | JWT signature verified server-side | `core/security.py` uses `jwt.decode(..., verify_signature=True)` with the Supabase secret | Pass |
| A2 | Expiry enforced | `options={"require": ["exp", "sub"], "verify_exp": True}` | Pass |
| A3 | Audience checked | `audience="authenticated"` | Pass |
| A4 | Rejection messages non-specific | `ExpiredSignatureError` and `InvalidTokenError` both yield generic text; the exception type is logged, not returned | Pass |
| A5 | Unauthenticated requests refused on every business endpoint | `test_api.py::TestAuthenticationRequired`, parameterized over 5 routes plus a malformed header case | Pass — automated |
| A6 | Session restored on reload without a new sign-in | `AuthProvider` calls `getSession()` on mount and subscribes to `onAuthStateChange` | Pass — manual |

## Multi-tenancy and authorization

| # | Control | Evidence | Result |
|---|---|---|---|
| B1 | RLS enabled on every tenant table | `test_migrations.py`, parameterized over all 13 tables | Pass — automated |
| B2 | Every RLS policy is org-scoped | `test_migrations.py::test_every_policy_is_org_scoped` parses each `CREATE POLICY` block and requires `auth_org_id()` | Pass — automated |
| B3 | `SECURITY DEFINER` functions pin `search_path` | `test_migrations.py::test_helper_functions_pin_search_path` | Pass — automated |
| B4 | Child tables re-check the parent relationship | `chunks_select`, `findings_select`, `evidence_select`, `reviews_insert` all include an `EXISTS` clause on the parent | Pass |
| B5 | Org id never taken from client input | `api/deps.py` resolves it from `profiles` by verified `sub`; grepped for header- or body-sourced org id — none found | Pass |
| B6 | Cross-tenant read returns 404 | `test_api.py::TestTenantIsolation::test_reading_another_orgs_document_is_404` | Pass — automated |
| B7 | 404 does not confirm existence | `test_the_404_does_not_confirm_existence` compares status **and message** between a real other-tenant document and a nonexistent id | Pass — automated |
| B8 | Cross-tenant write and delete blocked, with no side effect | `test_modifying_another_orgs_document_is_404` and `test_deleting_another_orgs_document_is_404` assert the target row is unchanged afterwards | Pass — automated |
| B9 | Policy administration requires admin or owner | `require_admin` dependency; `test_api.py::TestRoleAuthorization` covers member/admin/owner | Pass — automated |

## Secret handling

| # | Control | Evidence | Result |
|---|---|---|---|
| C1 | No secret committed | `.env` gitignored; CI runs `gitleaks` and fails on any committed `.env` | Pass |
| C2 | Service-role key absent from frontend | Grepped `frontend/` for `SERVICE_ROLE` — no matches. `lib/env.ts` exposes only the three safe values | Pass |
| C3 | Only safe values use `NEXT_PUBLIC_` | Reviewed `.env.example`: URL, anon key, API base URL | Pass |
| C4 | Redaction by key name, recursive | `core/logging.py::redact` walks dicts and lists to depth 6 against a 24-entry key set | Pass |
| C5 | Redaction by token pattern | Four regexes: Bearer, JWT shape, `sk-*`, `sk-ant-*` | Pass |
| C6 | Document text and quotes never logged | `text`, `normalized_text`, `chunk_text`, `quote`, `evidence`, `content` are in `SENSITIVE_KEYS`; strings truncate at 200 chars | Pass |
| C7 | Error tracker receives no PII | `sentry_sdk.init(..., send_default_pii=False)` | Pass |

## Input validation

| # | Control | Evidence | Result |
|---|---|---|---|
| D1 | Size limit enforced before parsing | `parse_upload` calls `validate_size` first; the router also caps the read at `max_bytes + 1` | Pass — automated |
| D2 | File type from magic bytes, not extension | `sniff_file_type`; `test_parsing.py::test_a_renamed_binary_is_rejected` | Pass — automated |
| D3 | ZIP without `word/document.xml` rejected as DOCX | `test_a_plain_zip_is_not_a_docx` | Pass — automated |
| D4 | Encrypted PDFs rejected | `parse_pdf` attempts an empty user password, then raises `EncryptedDocument` | Pass |
| D5 | Zip-bomb guard on DOCX | 200:1 ratio and 400 MB absolute ceiling, checked before `python-docx` opens the archive | Pass |
| D6 | Scanned PDFs rejected, not silently emptied | Text-density threshold; three tests including an explicit regression guard against the "accept a scan, report no risks" failure mode | Pass — automated |
| D7 | Page budget enforced | `MAX_DOCUMENT_PAGES`, applies to PDF, DOCX, and pasted text | Pass — automated |
| D8 | SQL is always parameterized | Reviewed all repositories: user input is exclusively `$n` bind parameters; f-strings interpolate only module-level constant column lists and an allowlisted `ORDER BY` key | Pass |

## AI-specific controls

| # | Control | Evidence | Result |
|---|---|---|---|
| E1 | Document text delimited as untrusted | `<document_chunk>` wrappers; system prompt states the boundary in seven explicit clauses | Pass — automated |
| E2 | Output schema has no severity field | `ProposedFinding` field set asserted in `test_prompt_injection.py` | Pass — automated |
| E3 | Injected extra fields rejected | `extra="forbid"`; a model emitting `"severity"` fails validation | Pass — automated |
| E4 | Policy weights never sent to the model | Rendered prompt scanned for `severity_weight`, `threshold`, `escalate` | Pass — automated |
| E5 | `score_finding` takes no document input | Signature asserted exactly; source grepped for `document` and `chunk` | Pass — automated |
| E6 | Cross-document tool calls rejected | `ToolExecutor._search` and `_neighbours` compare against the context document and refuse; tested with the fixture's own injected UUID | Pass — automated |
| E7 | Unknown tool names refused, budget not consumed | `test_tools.py` | Pass — automated |
| E8 | Tool loop bounded | 5 calls per category plus an 8-iteration hard stop; `test_an_infinite_tool_loop_terminates` feeds 30 consecutive tool responses and asserts termination | Pass — automated |
| E9 | Unverifiable evidence quarantined | `verify_evidence` returns `QUARANTINED`; no evidence row is written; excluded from the score | Pass — automated |
| E10 | Fabricated quotes never displayed | `TestFabricatedEvidence`, five tests including a one-word substitution | Pass — automated |

## Transport and browser

| # | Control | Evidence | Result |
|---|---|---|---|
| F1 | CSP present and restrictive | `frame-ancestors 'none'`, `object-src 'none'`, `default-src 'self'` | Pass — automated |
| F2 | HSTS in production | Set when `ENVIRONMENT` is staging or production | Pass |
| F3 | MIME sniffing disabled | `X-Content-Type-Options: nosniff` | Pass — automated |
| F4 | Framing denied | `X-Frame-Options: DENY` plus CSP | Pass — automated |
| F5 | Production CORS wildcard refused at startup | `Settings` validator raises; `test_production_rejects_a_wildcard_origin` | Pass — automated |
| F6 | Test-only embedding provider refused in production | `Settings` validator raises; `test_production_rejects_the_test_embedding_provider` | Pass — automated |

## Availability and abuse

| # | Control | Evidence | Result |
|---|---|---|---|
| G1 | Per-org analysis rate limit | Redis sliding window, default 20/hour | Pass |
| G2 | Per-org request rate limit | Default 200/minute | Pass |
| G3 | `429` carries `Retry-After` | Set in the `AppError` handler; asserted in `test_api.py` | Pass — automated |
| G4 | Limiter fails open on Redis outage | `test_the_limiter_fails_open_when_redis_breaks` | Pass — automated |
| G5 | Duplicate analyses prevented | Partial unique index on `(document_id, policy_id)` for live rows, plus a Redis dedupe key and an idempotency-key index | Pass |

## Auditability

| # | Control | Evidence | Result |
|---|---|---|---|
| H1 | All mutating actions audited | 13 action constants covering documents, analyses, policies, and findings | Pass |
| H2 | Audit metadata sanitized independently | `safe_metadata()` strips sensitive keys and caps sizes, separately from the log redactor | Pass |
| H3 | Review history append-only | No `UPDATE`/`DELETE` policy on `finding_reviews`; asserted in `test_migrations.py` | Pass — automated |
| H4 | Machine decision never overwritten | `add_review` writes `override_severity` and `severity_source`, never `machine_severity`; a review row records both previous and new values | Pass — automated |

---

## Residual risk

Honest list of what is *not* covered.

1. **RLS is verified by static analysis, not by execution.** The tests parse the
   migration with the real PostgreSQL parser and assert structural properties.
   They do not connect two authenticated sessions to a live Supabase instance
   and attempt a genuine cross-tenant read. The API-level isolation tests do run
   the real dependency chain, but against in-memory repositories. **To close
   this:** add an integration suite that provisions two Supabase users and
   asserts empty result sets across the boundary.

2. **No penetration test.** This is a self-audit by the implementer.

3. **Dependency advisories are warnings, not gates.** CI runs `pip-audit`,
   `npm audit`, and `bandit`, but they log warnings rather than failing the
   build, to avoid unrelated upstream advisories blocking merges. `gitleaks` and
   the committed-`.env` check *do* fail the build.

4. **The rate limiter fails open.** A deliberate availability trade-off: a Redis
   outage does not take down the API, but it also does not limit during one.

5. **Signed URLs are bearer credentials.** A 5-minute signed URL, if leaked
   within its window, grants access. TTL is configurable.

6. **The service role bypasses RLS.** Necessary for the worker. It means a
   backend code path that omits its explicit `org_id` filter would not be caught
   by RLS. Mitigated by every repository method taking `org_id` as a required
   parameter — but it is a real dependency on code correctness.

7. **Prompt-injection defence is layered, not proven.** The structural
   controls (no severity field, weights withheld, tools scoped, evidence
   verified) hold regardless of whether the model is persuaded. The prompt-level
   instruction is best-effort. The correct reading is: injection cannot
   fabricate evidence or alter severity, but it could in principle cause the
   model to under-report — which is why abstention is recorded explicitly and is
   distinguishable from a clean pass.

8. **Not deployed.** These controls have not been exercised against real
   internet traffic.
