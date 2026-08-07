# Implementation status

Last updated: 2026-08-02

---

## Phase 0 — Repository audit (baseline)

The audit was performed first, as required. Its finding was that **there was no
existing repository to audit**.

| Checked | Result |
|---|---|
| Target directory `C:\Users\balap\Downloads\EULA` | Empty — zero files, including dotfiles |
| Attached project knowledge (`my final eula project`) | Contained only `metadata.json`; no `docs/`, `files/`, or `memory.md` |
| `plan.md`, `design.md`, `README.md` | Absent |
| Existing source, migrations, tests, Docker, CI | Absent |
| `docs/diagrams/` Excalidraw, PNG, SVG files | Absent |
| Existing frontend / backend architecture | None |
| `TODO` / `FIXME` / mock data / hard-coded results | Nothing to scan |
| Frontend install, lint, typecheck, build baseline | Not applicable — no frontend existed |
| Backend tests, lint, typecheck baseline | Not applicable — no backend existed |

This was raised as a blocker before any code was written, and the user confirmed
building from scratch. **No existing work was overwritten, and no diagrams were
replaced or renamed, because none existed.**

Because there was no prior repository, the instruction to preserve existing
diagrams and filenames could not be honoured literally. Six new architecture
diagrams were authored as Mermaid sources in `docs/diagrams/`, which GitHub
renders inline and which remain editable.

---

## Completed

### Phase 1 — Project configuration
`.env.example` with every documented variable; Pydantic-settings validation that
fails loudly and names missing variables; production refuses a CORS wildcard and
the test-only embedding provider; backend `Dockerfile` (non-root, healthcheck);
`docker-compose.yml` (Postgres+pgvector, Redis, API, worker, migrate, seed,
optional frontend); `Makefile`; PowerShell / Git Bash / Linux instructions in the
README.

### Phase 2 — Database schema
10 numbered SQL migrations building the entire schema from zero. 14 tables:
`organizations`, `profiles`, `documents`, `document_chunks`, `embedding_cache`,
`policies`, `policy_rules`, `analyses`, `analysis_categories`, `findings`,
`finding_evidence`, `finding_reviews`, `audit_logs`, `usage_events`. UUID keys,
timestamps, cascade rules, check constraints on every severity/confidence/score
column, partial and unique indexes, pgvector HNSW index, generated `tsvector`
column with a GIN index, content hashes, exact offsets, soft delete, policy
versioning, deterministic severity provenance, human override records.
Reproducible idempotent seed with the default policy and 12 categories.

### Phase 3 — Authentication and multi-tenancy
Supabase registration, login, logout, session restoration, optional Google
OAuth; FastAPI JWT verification (signature, expiry, audience); org and profile
provisioned by a database trigger inside the signup transaction; owner/admin/
member roles; RLS on all 13 tenant tables routed through a single `auth_org_id()`
helper; API-level ownership checks on every query; frontend route guards.

### Phase 4 — Document upload
PDF, DOCX, TXT, and pasted text. Magic-byte sniffing with extension as a
secondary signal; 10 MB and 150-page budgets; encrypted-PDF rejection;
zip-bomb guard; **scanned-PDF detection with an actionable message**; private
org-prefixed storage; short-lived signed URLs only.

### Phase 5 — Parsing and normalization
Single authoritative normalization (NFKC, unicode spaces, invisible characters,
smart punctuation, line endings, whitespace), SHA-256 content hash, stored
normalized text, metadata, status transitions.

### Phase 6 — Clause-aware chunking
Segments on numbered sections, lettered subclauses, ALL-CAPS headings, and
paragraph breaks. 200–600 token target, 800 maximum, 60-token merge floor,
sentence-boundary splitting for oversized clauses. Exact offsets with the
invariant `text[start:end] == chunk.text` asserted by tests.

### Phase 7 — Embedding provider
`EmbeddingProvider` interface with batching, bounded retries, rate-limit
handling, dimension validation, content-hash caching, and usage/cost tracking.
OpenAI and Voyage implementations; a deterministic offline provider for tests
that production configuration refuses to load.

### Phase 8 — Hybrid RAG
Per-category query construction; pgvector cosine search; PostgreSQL full-text
search; Reciprocal Rank Fusion; neighbouring-clause retrieval; four-tier
fallback chain (hybrid → dense → keyword → bounded ordinal scan) with the
degraded mode recorded, confidence capped, severity capped, and a visible UI
warning. Retrieval evaluation script with a labelled set.

### Phase 9 — Background jobs
Durable Redis reliable queue (`BRPOPLPUSH`), atomic status-guarded job claiming,
9 persisted stages, resumable per-category progress, duplicate prevention
(partial unique index + Redis dedupe key + idempotency key), retries, worker
heartbeat, stalled-run recovery sweep, queue health reporting.

### Phase 10 — Claude integration
Official Anthropic SDK; `LLMProvider` abstraction; model read from config in
exactly one place (enforced by a test); configurable timeouts; bounded retries
with exponential backoff and jitter; rate-limit and overload handling;
non-retryable 4xx distinguished from transient failures; input, cached-input,
and output token recording; cost estimation; strict Pydantic output validation
with exactly one repair attempt then `needs_review`.

### Phase 11 — Tool use
Three bounded read-only tools (`search_document`, `get_neighboring_chunks`,
`flag_for_review`) with input validation, unknown-name rejection, organization
and document ownership enforcement, cross-document denial, result size caps, 5
calls per category, and an 8-iteration hard stop.

### Phase 12 — Prompt-injection defence
Explicit untrusted-data boundary in the system prompt; labelled chunk
delimiters; no severity field in the schema with `extra="forbid"`; policy
weights never in a prompt; tools scoped to one document; severity computed from
two numbers only. Adversarial fixture and 20 tests.

### Phase 13 — Evidence verification
Chunk existence, document ownership, organization ownership, normalized quote
matching, offsets recomputed from the match, quarantine of unsupported evidence,
`needs_review` for uncertain cases. `offset_exact` and `offset_normalized`
methods. **Permanent fabricated-evidence regression test.**

### Phase 14 — Deterministic scoring
`weighted_risk = confidence × weight`, band mapping, threshold demotion,
escalation promotion, degraded-retrieval cap. All inputs and a plain-English
explanation persisted. 0–100 saturating document score with four risk bands.
Human review preserving full history without mutating the machine decision.
Executive summary generated only from verified, persisted, scored findings.

### Phase 15 — REST API
All specified endpoints plus `/documents/{id}/text`, `/policies/{id}/versions`,
`/findings/{id}/reviews` (GET), and `/dashboard`. Authentication, authorization,
ownership checks, role checks, pagination, search, filters, sorting, validation,
consistent response models, and the full status-code set behind one error
envelope.

### Phase 16 — Frontend
13 routes: landing, register, login, dashboard, document library, upload/paste,
document detail, analysis progress, findings workspace, policy list, policy
editor, usage, admin metrics, plus error and not-found pages. Real backend
integration, loading/empty/error/partial states, evidence highlighting from
stored offsets, reviewer actions, visible "not legal advice" notice.

### Phases 17–18 — Security and observability
JWT verification, RLS, ownership checks, admin checks, private storage, signed
URLs, strict CORS, security headers, rate limiting, upload validation,
prompt-injection protection, request IDs, audit logging, secret redaction,
worker heartbeats, health and readiness checks, usage and cost tracking,
optional Sentry, structured JSON logging, per-stage timings, admin metrics.

### Phase 19 — Testing
339 backend tests, 11 frontend tests, Playwright E2E workflow specs. No paid
provider call in the default run.

### Phase 20 — Evaluation
`docs/EVALUATION.md` with measured results, explicit "not measured" entries, and
instructions for measuring the rest.

### Phase 21 — Demo data
`scripts/seed.py --demo` writes through the ordinary schema and flags the
organization `is_demo`. Production paths never generate demo content.

### Phases 22–23 — Docker and CI
One backend image with API and worker entrypoints; compose stack with
healthchecks and migrate/seed profiles. GitHub Actions with four jobs: backend
(lint, format, types, tests, retrieval evaluation), migrations (applied twice to
a clean pgvector Postgres, then seeded), frontend (lint, typecheck, tests,
production build), and security (gitleaks, bandit, pip-audit, npm audit, and a
hard failure if a `.env` is committed).

### Phases 24–25 — Deployment and documentation
`docs/INFRASTRUCTURE.md` with the full procedure and every unperformed step
marked. README, `docs/SECURITY.md`, `docs/SECURITY_AUDIT.md`,
`docs/EVALUATION.md`, and six Mermaid architecture diagrams.

---

## Verification results

Every command below was executed. These are the actual outputs.

| Command | Result |
|---|---|
| `ENVIRONMENT=test pytest -q` (backend) | **339 passed** |
| `ruff check app scripts tests` | **All checks passed!** |
| `ruff format --check app scripts tests` | **71 files already formatted** |
| `mypy app` | **Success: no issues found in 53 source files** |
| `python -m scripts.evaluate_retrieval` | **recall@8 = 100.0% (12/12), target met** |
| `python -m scripts.evaluate_retrieval -k 1` | **recall@1 = 100.0% (12/12)** |
| `npx tsc --noEmit` (frontend) | **Exit 0** |
| `npx next lint` | **No ESLint warnings or errors** |
| `npx vitest run` | **11 passed (1 file)** |
| `npx next build` | **Compiled successfully — 13 routes** |
| Migration SQL parsed with libpg_query | **All 10 migrations parse** (60 tests) |

Environment used for verification: Python 3.10 sandbox (the project targets
3.11+; code is written to run on both), Node 22.22.3, Next.js 14.2.35,
PostgreSQL 16.2 with pgvector 0.6.2.

### Migration execution (real database)

Executed against a real PostgreSQL 16.2 server with pgvector, seeded with the
`auth` and `storage` surface Supabase provides:

| Step | Result |
|---|---|
| `python -m scripts.migrate` from an empty database | **10 applied, 0 already present** |
| `python -m scripts.migrate` again | **0 applied, 10 already present** (idempotent, checksums recorded) |
| `python -m scripts.seed --demo` | Created demo org, default policy, 12 categories, demo document + 12 chunks |
| `python -m scripts.seed --demo` again | No changes (idempotent) |
| Tables created | **15 / 15** |
| RLS-enabled tables / policies | **13 / 25** |
| Indexes (incl. HNSW vector + GIN fts) | **58** |
| `handle_new_user` trigger | Auto-provisioned an organization + owner profile per user |
| `auth_org_id()` isolation | Two users resolved to **different** organizations; returns NULL with no JWT |
| `auth_is_admin()` | `true` for owner, `false` for member |

### Bugs found and fixed during this verification

1. **Migration 0001 referenced `profiles` before it existed.** The `auth_org_id`,
   `auth_role`, and `auth_is_admin` helpers are `LANGUAGE sql`, whose bodies
   PostgreSQL validates at `CREATE` time, so migrating a genuinely empty database
   failed with `UndefinedTableError: relation "profiles" does not exist`. The
   helpers now live at the end of 0002, immediately after `profiles`.
   **Why the test suite missed it:** migrations were validated with libpg_query,
   which checks grammar, not catalog dependency order. `TestDependencyOrder` now
   covers exactly that, and was confirmed to fail when the bug is reintroduced.
2. **`CORS_ORIGINS` crashed startup.** pydantic-settings JSON-decoded the list
   field before the validator ran, so the plain comma-separated value documented
   in `.env.example` was rejected. Both formats are now accepted.
3. **Extension requirements were too strict.** `pgcrypto` (redundant on
   PostgreSQL 13+) and `pg_trgm` (backs one non-critical search index) now
   degrade with a notice instead of failing the whole schema. `vector` remains a
   hard requirement and fails loudly with remediation instructions.

---

## Files

**Added:** 71 backend Python files (~11,200 lines), 28 frontend TypeScript files
(~3,500 lines), 10 SQL migrations, 12 test modules, 2 test fixtures, 1 labelled
evaluation set, 6 Mermaid diagrams, 5 documentation files, `Dockerfile` ×2,
`docker-compose.yml`, `Makefile`, `.env.example`, `.gitignore`, CI workflow.

**Changed:** none — the repository was empty at the start.

---

## Not done, and why

| Item | Reason |
|---|---|
| **Production deployment** | No Supabase project, Anthropic key, embedding key, Vercel account, or Render/Railway account was available. `docs/INFRASTRUCTURE.md` documents every step; each unperformed one is marked. **No deployment URL is claimed.** |
| ~~Migrations applied to a live Postgres~~ | **Now done.** All 10 migrations were executed against a real PostgreSQL 16.2 + pgvector 0.6.2 server from an empty database, run twice for idempotency, then seeded twice. See "Migration execution" below. |
| **Live cross-tenant RLS test** | Requires two authenticated Supabase sessions. API-level isolation is tested against the real dependency chain; RLS itself is verified structurally. Recorded as residual risk #1 in `docs/SECURITY_AUDIT.md`. |
| **Real Claude and embedding runs** | No credentials. Every AI path is exercised through test doubles, including rate limits, overload, malformed output, tool loops, and injection. |
| **Playwright E2E execution** | Requires a running stack and credentials. The specs are written and **skip rather than silently pass** when those are absent. |
| **Clause recall, category precision, verification pass rate, needs-review rate, override rate, latency percentiles, cost per analysis** | All require real runs against a labelled corpus. Instrumentation exists for every one; see `docs/EVALUATION.md`, where each is marked **not measured**. |
| **OCR for scanned PDFs** | Deliberate. Introducing transcription errors into the evidence chain conflicts with the verification guarantee. Scans are rejected with a clear message instead. |
| **Non-English agreements** | The FTS index uses the `english` configuration and the category definitions are English. |

---

## External configuration required before first run

1. A Supabase project — URL, anon key, service-role key, JWT secret, database URL.
2. An Anthropic API key.
3. An embedding provider API key (OpenAI or Voyage).
4. A Redis instance (local via Docker Compose, or Upstash in production).
5. `.env` populated from `.env.example`.
6. `python -m scripts.migrate` then `python -m scripts.seed`.

---

## Blockers

None outstanding for local development.

For production: credentials for the five external services above. Every step
that depends on them is listed and marked in `docs/INFRASTRUCTURE.md`.


---

## Cloud integration status

Last verified: 2026-08-04

### Honest one-line status

> AWS S3 and SES integrations are implemented behind feature flags and validated with botocore Stubber. Live AWS verification is pending.

### What is complete and verified

| Item | Evidence |
|---|---|
| `ReportStorageProvider` abstraction | Supabase and S3 both implement it; factory selects by flag |
| `S3ReportStorageProvider` | 32 tests via botocore `Stubber`; SigV4 presigning confirmed |
| `SesEmailProvider` | Slots into the existing `EmailProvider` ABC; MIME and error handling tested |
| Feature-flag defaults | Both `false`; startup validation rejects an enabled flag with a missing bucket/sender |
| Failure isolation | Tests assert no AWS module references `analyses`, `findings`, `machine_severity` |
| Full suite | **576 backend tests pass**, ruff/format/mypy clean, frontend builds |

### What is disabled by feature flags

- `AWS_REPORT_STORAGE_ENABLED=false` — reports go to Supabase Storage
- `AWS_SES_ENABLED=false` — email uses `EMAIL_PROVIDER` (default `console`)

### What requires cloud-console setup before it can be verified

1. S3 bucket with Block Public Access and default encryption
2. IAM role/user with the least-privilege policy in `docs/INFRASTRUCTURE.md` § A3
3. SES verified sender, and a verified recipient while in sandbox
4. Local AWS credentials via the standard chain

### Known limitations

- **No AWS call has run against real AWS.** Stubber validates request shapes,
  not IAM, bucket policy or SES account state.
- Lambda, CloudWatch metrics and KMS have not been implemented.
- **No Google Cloud integration exists.** Vertex AI/Gemini second review,
  BigQuery analytics and Cloud Run export were all deferred.
- ClauseGuard must not be described as multi-cloud.

### Core application — still unverified end to end

The core has **never processed a real agreement**: no Anthropic or embedding key
has been configured, so every AI path is exercised only through test doubles.
This is the highest-priority gap, ahead of any cloud work. See
`docs/SETUP_CHECKLIST.md`.


---

## Deployment status

Last updated: 2026-08-06

### Verified in production

**Nothing.** The application has not been deployed. No Vercel, Render, Supabase
production or Upstash resource has been provisioned, and no live URL exists.

### Verified locally / in CI

| Area | Status |
|---|---|
| Backend suite | **583 tests pass** |
| Lint / format / types | ruff clean, 93 files formatted, mypy clean on 70 files |
| Migrations | All 12 apply to a real PostgreSQL 16 + pgvector, twice (idempotent), then seed |
| Frontend | lint, typecheck, 18 tests, production build all passed earlier in this session; **source unchanged since** |
| Container port binding | Dockerfile `CMD` binds `${PORT}`; expansion verified for Render (10000) and compose (8000) |
| Render Blueprint | `render.yaml` defines both services; tests assert no secret carries a literal value |

### Deployment blockers fixed in this pass

1. **Dockerfile could never bind Render's `$PORT`.** The exec-form `CMD`
   hardcoded 8000 and would have passed the literal string `$PORT` had it been
   templated. Render's port detection would have failed and the service would
   never have gone live. Now shell form with `${PORT}` and an `ENV PORT=8000`
   default so docker-compose is unaffected. Pinned by tests.
2. **No `render.yaml`.** Added, defining API and worker from one image.
3. **`HEALTHCHECK` hardcoded port 8000**, which would have broken once the app
   bound a different port. Now follows `${PORT}`.

### Still required before deployment

1. **Commit and push to GitHub** — the repository has **0 commits**. Vercel and
   Render deploy from GitHub and cannot see a local folder. This is the single
   hard blocker.
2. Anthropic and embedding API keys.
3. Supabase production project, Upstash Redis instance.
4. Provision the three services and wire origins (`docs/INFRASTRUCTURE.md` § D1–D6).

### Cloud integration status

- **AWS** — S3 and SES implemented behind disabled feature flags and validated
  with offline AWS API stubs. Live AWS verification is pending.
- **Google Cloud** — integration has not yet been implemented.

The project must not be described as multi-cloud.
