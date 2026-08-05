# plan.md — ClauseGuard

**Project planning document for the build phase.**

> **Placeholders to fill before submission.** Search this file and `README.md`
> for `TODO:` — Z-number, FAU email, deployed URL, and demo video URL are the
> only facts this document cannot supply for itself.

---

## 1. Project Summary

### 1.1 Project title

**ClauseGuard — Automated EULA Compliance Extraction**

The official project topic is **Automated EULA Compliance Extraction**.
*ClauseGuard* is the application name only.

| Field | Value |
|---|---|
| Student | aish |
| Z-number | `TODO: Z########` |
| FAU email | `TODO: @fau.edu` |
| Repository | GitHub Classroom (main branch) |
| Deployed app | `TODO: https://…` (see §3, Week 2) |
| Demo video | `TODO: https://…` (see §3, Week 2) |

### 1.2 Problem statement and sponsor

**Selected problem statement:** organizations agree to software contracts they
have not read, and cannot afford to have a lawyer read every one.

**Sponsor:** none — self-directed problem selection.

### 1.3 Target users and stakeholders

| Stakeholder | Role | What they need from the system |
|---|---|---|
| **Compliance / legal-ops analyst** | Primary user | Triage inbound vendor agreements fast; decide what needs escalation |
| **Procurement manager** | Primary user | Decide which contracts need paid legal review |
| **Founder / small-business owner** | Primary user | Understand what they are about to sign, without counsel |
| **Privacy & security reviewer** | Secondary user | Check retention, sharing and subprocessor terms against internal policy |
| **Organization owner / admin** | Administrator | Configure the compliance policy; control who can change severity weights |
| **In-house counsel** | Reviewer of record | Trust, or override, the machine's judgement — and see why it decided that |
| **Vendor (counterparty)** | Affected third party | Not a user, but their document is the input; must never be able to influence the verdict |

### 1.4 Core value proposition

The clauses that create real exposure — a perpetual licence over customer
content, a liability cap of fifty dollars, a ninety-day non-renewal window, an
indemnity that survives the vendor's own negligence — sit inside 15–60 pages of
prose that looks identical to the boilerplate around them. Manual review does
not scale.

Naive AI review is *worse* than no review. A language model asked to "find the
risky clauses" will produce a fluent, confident quote that appears nowhere in
the document. In a compliance tool this is not a quality problem, it is a
correctness problem: a fabricated finding is indistinguishable from a real one
to the reader, and it is more dangerous than silence because it is trusted.

**ClauseGuard's differentiator is that it refuses to trust the model's output.**

| Failure mode of naive AI contract review | ClauseGuard's structural answer |
|---|---|
| The model quotes text that isn't in the document | Every quote is located in the stored source before persistence. Unverifiable evidence is **quarantined**, excluded from the score, never shown as confirmed |
| The model decides how serious a clause is | The model reports *confidence only*. Severity is computed in application code from the organization's own weights, which never enter a prompt |
| A contract containing "ignore previous instructions" changes the verdict | Five layers: untrusted-data delimiters, no severity field in the output schema, weights withheld, tools scoped to one document, deterministic scoring |
| Retrieval silently misses a clause and the report looks clean | Every retrieval degradation is recorded, caps confidence, caps severity, and surfaces as a visible warning |
| One bad category kills the whole run | Categories fail independently; the analysis completes as `partial` with those categories marked `needs_review` |
| The AI's judgement overwrites the reviewer's | Human overrides are stored separately; `machine_severity` is never mutated; review history is append-only |

**Success in one sentence:** a reviewer can click any finding and see the exact
sentence in the source contract that produced it — or be told plainly that the
system could not substantiate it.

---

## 2. Requirements

### 2.1 Core Requirements (Week 3 Gate)

| Gate requirement | How this plan addresses it |
|---|---|
| **AI Integration** | Not a chatbot wrapper. A six-stage analysis pipeline: clause-aware chunking → hybrid RAG retrieval → per-category Claude extraction with bounded tool use → evidence verification → deterministic scoring → summary. Error handling covers provider rate limits, overload, timeouts, malformed structured output (one repair attempt, then `needs_review`), and tool-loop exhaustion. The UI has explicit loading, empty, error, **partial-analysis** and **degraded-retrieval** states. Rate limits return `429` with `Retry-After`. Every error is a plain-English message plus a correlation ID. |
| **Backend & Database** | Supabase Postgres with 14 tables, full CRUD on documents, policies, policy rules, analyses, findings and reviews. Numbered SQL migrations build the schema from zero. Constraints, indexes, partial indexes, cascade rules, soft delete and content hashing are specified in `design.md` §4. |
| **Authentication** | Supabase Auth: registration, login, logout, session restoration, optional Google OAuth. Protected frontend routes, JWT verification in FastAPI, organization/role model (`owner`/`admin`/`member`), Row-Level Security on every tenant table. All secrets in environment variables; the service-role key never reaches the browser. |
| **Documentation** | `README.md` (name, Z-number, FAU email, deployed link, demo video, description, AI explanation, setup, tech stack), plus `plan.md`, `design.md`, and supporting `docs/INFRASTRUCTURE.md`, `docs/SECURITY.md`, `docs/SECURITY_AUDIT.md`, `docs/EVALUATION.md`, `docs/IMPLEMENTATION_STATUS.md`. |
| **Deployment** | Vercel (frontend) + Render/Railway (API and worker from one image) + Supabase + Upstash Redis. Scheduled in §3 Week 2. **Not yet deployed; no URL is claimed anywhere in this repository until it is.** |
| **GitHub Repository** | Public GitHub Classroom repo, `main` branch, `.gitignore` excluding `.env`, `.gitattributes` normalising line endings, CI failing the build if a `.env` is ever committed. Commit discipline in §3.3. |
| **Demo Video** | 3–5 minutes, scripted in §3.4, recorded in Week 2 against the deployed instance. |
| **Canvas Submission** | Repository link submitted per assignment; checklist in §3.5. |

### 2.2 Build-Phase Requirements

#### 2.2.1 Problem Selection & Technical Specification

**Domain research.** Consumer- and business-facing agreements share a small,
stable set of recurring risk categories. The twelve the default policy ships
with — data retention, data sharing, subprocessors, IP ownership, content
licensing, automatic renewal, cancellation, indemnification, limitation of
liability, governing law, arbitration, class-action waiver — were chosen because
they map to the concerns that actually appear in privacy and procurement
checklists (GDPR/CCPA-style retention and sharing duties; standard commercial
terms around renewal, liability and dispute resolution).

**Constraints.**

| Constraint | Consequence for the design |
|---|---|
| A hallucinated citation is unacceptable | Verification gate is mandatory, not advisory |
| A document is attacker-controlled input | Prompt-injection defence must be structural, not just instructional |
| LLM calls cost money and time | Retrieve, never send the whole contract; cache embeddings by content hash |
| Legal text is hierarchical | Clause-aware chunking, not fixed windows |
| Multi-tenant confidential data | Three independent authorization layers |
| Providers fail | Per-category isolation; partial results beat failed runs |
| Student budget | Free tiers wherever possible; hard per-org rate limits |

**Key technical challenges and mitigations.**

1. *Proving a quote is real* → normalize once, store offsets, re-locate the quote and recompute offsets from the match.
2. *Keeping severity out of the model's reach* → no severity field in the schema; `extra="forbid"`; weights never in prompts.
3. *Finding the right clause* → hybrid dense + full-text retrieval fused with Reciprocal Rank Fusion.
4. *Surviving provider failure* → bounded retries with jitter; category-level isolation; `partial` status.
5. *Not losing jobs* → Redis reliable queue plus atomic claim in Postgres, heartbeats, resumable per-category progress.

**Technical feasibility study.**

| Question | Finding | Verdict |
|---|---|---|
| Can quotes be verified reliably given model whitespace drift? | Normalize both sides identically; recover exact offsets through a character index map | **Feasible** |
| Can pgvector serve semantic search at this scale? | HNSW index, cosine distance; documents are ≤150 pages ⇒ low hundreds of chunks each | **Feasible** |
| Can hybrid retrieval beat dense-only on legal terms of art? | FTS reliably catches "indemnify", "perpetual, irrevocable", "class action" that embeddings smooth over; RRF needs no score calibration | **Feasible** |
| Can we bound cost per analysis? | 12 categories × retrieved chunks only, not full document; embeddings cached by hash | **Feasible** — see §2.2.4 |
| Can we prevent cross-tenant leakage with a service-role worker? | RLS at DB + explicit `org_id` scoping in every repository method + route guards | **Feasible with discipline** — logged as residual risk |
| OCR for scanned PDFs? | Would inject transcription error into the evidence chain, contradicting the core guarantee | **Rejected** — reject scans with a clear message instead |

**System architecture, data flow and user flow diagrams:** see `design.md`
§1–§3 (six Mermaid diagrams in `docs/diagrams/`).

**Technology stack justification:** see `design.md` §8.

**Database schema and API structure:** see `design.md` §4 and §5.

**Weekly milestones, critical path and dependencies:** §3 below.

**Success metrics and KPIs.**

| KPI | Target | How measured |
|---|---|---|
| Fabricated quotes displayed | **0%** | Structural guarantee + permanent regression test |
| Retrieval recall@8 | ≥ 90% | `scripts/evaluate_retrieval.py` against a labelled set |
| Clause recall | ≥ 90% | Labelled corpus vs. verified findings |
| Category precision | ≥ 85% | Labelled corpus |
| Verification pass rate | ≥ 95% | `analyses.verification_pass_rate`, surfaced on admin metrics |
| Needs-review rate | 5–15% | `analysis_categories.status` distribution |
| Reviewer override rate | < 20% | `finding_reviews` where `action='override_severity'` |
| API p95 latency | < 500 ms | Request duration logs |
| DB p95 query time | < 100 ms | Postgres statistics |
| 30-page analysis p95 | < 90 s | `analyses.stage_timings_ms` |
| Uptime | > 99.5% | Host platform monitoring on `/health` |
| Error rate | < 1% | `analyses` failed / total |
| Cost per analysis | < $0.20 | `analyses.estimated_cost_usd` |

**MVP scope vs. nice-to-have.**

| MVP (must ship) | Nice-to-have (only if time allows) |
|---|---|
| Auth, orgs, roles, RLS | Multi-user org invitations |
| Upload PDF/DOCX/TXT + paste | OCR for scanned PDFs *(deliberately excluded)* |
| Clause-aware chunking with exact offsets | Cross-chunk clause stitching |
| Hybrid RAG + RRF + fallback chain | Reranker model |
| Claude extraction + bounded tools | Multi-agent orchestration |
| Evidence verification + quarantine | Diff view across contract versions |
| Deterministic scoring + reviewer workflow | Bulk contract comparison |
| Policy editor with versioning | Policy import/export |
| Usage, cost, admin metrics | Budget alerting via email |
| Docker, CI, deployment, docs, demo video | Expanded labelled evaluation corpus |

#### 2.2.2 Agentic AI & RAG

**RAG components.**

| Item | Decision | Rationale |
|---|---|---|
| **Vector database** | **pgvector inside Supabase Postgres** | Rejected Pinecone/Weaviate/Chroma: a separate vector store means a second system to secure, a second place tenancy can leak, and no transactional consistency between a chunk and its embedding. pgvector keeps embeddings in the same row as the text, under the same RLS policy, in the same backup. |
| **Index** | HNSW, `vector_cosine_ops`, `m=16`, `ef_construction=64` | Better recall/latency than IVFFlat and needs no training step, so it is correct from the first inserted row |
| **Ingestion** | Normalize once → store as authoritative text → clause-aware chunk → embed → store | One coordinate space for all offsets |
| **Chunking strategy** | Segment on numbered sections, lettered subclauses, roman numerals, ALL-CAPS headings, paragraph breaks. Target 200–600 tokens, max ~800, merge floor 60 tokens, sentence-boundary split for oversized clauses | Fixed windows cut obligations in half; legal documents already carry explicit structure |
| **Embeddings** | `EmbeddingProvider` interface; OpenAI `text-embedding-3-small` (1536-d) in production, Voyage supported, deterministic offline provider for tests | Anthropic has **no** embeddings API, so this abstraction is deliberately independent of the LLM provider |
| **Embedding cache** | Keyed by SHA-256 of normalized chunk text | Re-analysing the same agreement re-embeds nothing |
| **Semantic search** | pgvector cosine + PostgreSQL `ts_rank_cd` full-text, fused with **Reciprocal Rank Fusion** (`score = Σ 1/(60+rank)`) | Dense finds meaning, FTS catches terms of art; RRF uses rank only, so the two incomparable score scales need no calibration |

**Agentic patterns.**

| Pattern | Implementation |
|---|---|
| **Multi-step task design** | Six persisted stages (parse → chunk → retrieve → extract → verify → score). Extraction loops per policy category, each an independent unit of work |
| **Tool / function calling** | Three bounded, **read-only** tools: `search_document` (more chunks from *this* document), `get_neighboring_chunks` (adjacent clauses), `flag_for_review` (hand the category to a human) |
| **Agent memory & context retention** | Per-category `ToolContext` carries org ID, document ID, analysis ID, accumulated chunk IDs and call count. Conversation history accumulates within a category; nothing persists across categories, so one category cannot contaminate another |
| **Orchestration logic** | `AnalysisPipeline` sequences stages and persists progress after each category. `analyses.completed_categories` makes a restarted worker resume rather than restart |
| **Bounding** | Max 5 tool calls per category; 8-iteration hard stop; exceeding either ends the category as `needs_review` rather than looping |

**Integration and user interaction.** Users never talk to the agent. They
upload a document, choose a policy, and press *Start analysis*. The API returns
`202 Accepted` immediately and the UI polls real persisted stage progress. The
agentic behaviour surfaces only as outcomes: a category the model chose to flag
appears as `needs_review` with its stated reason; extra context the model pulled
in is reflected in the chunks cited. Every finding links to highlighted source
text.

**Caching and fallback strategies.**

```
Retrieval fallback chain
  hybrid (dense + FTS + RRF)     confidence ceiling 1.00
        ↓ FTS unavailable
  dense only                     confidence ceiling 0.85
        ↓ vector unavailable
  keyword only                   confidence ceiling 0.70
        ↓ both unavailable
  bounded ordinal scan           confidence ceiling 0.55
```

Every degradation is recorded on the analysis, caps severity at `high`, and
renders a visible warning. Failures are never hidden. Caches: embeddings by
content hash; Anthropic prompt-cache token accounting; Redis for rate-limit
windows and job dedupe.

#### 2.2.3 Production Engineering

| Area | Plan |
|---|---|
| **Containerization** | One backend image, two entrypoints (`uvicorn app.main:app` / `python -m app.worker`) so API and worker cannot drift. `python:3.11-slim`, layer-cached dependency install, non-root user (uid 10001), `HEALTHCHECK`, `.dockerignore`. Frontend uses a 3-stage build to Next.js `standalone`. `docker-compose.yml` runs Postgres+pgvector, Redis, API, worker, plus `migrate`/`seed` tool profiles |
| **Observability** | Structured JSON logging to stdout with request correlation IDs, analysis IDs and org IDs; per-stage timings persisted in `analyses.stage_timings_ms`; token counts and estimated cost per analysis; optional Sentry (`send_default_pii=False`); `/health` (liveness) and `/health/ready` (dependency health); worker heartbeats; `/api/v1/admin/metrics` dashboard showing success/error rate, verification pass rate, stage latency, p95 analysis time, queue depth and live workers |
| **Log safety** | Recursive redaction by key name (24 keys incl. `quote`, `chunk_text`, `normalized_text`, tokens, service-role key) and by pattern (`Bearer …`, JWT shape, `sk-*`, `sk-ant-*`); strings truncated at 200 chars. Full document text and complete evidence quotes are never logged |
| **Database optimization** | Indexes on every hot path; **partial** indexes matching real query shapes (`WHERE deleted_at IS NULL`, `WHERE verification_status='verified'`, `WHERE status IN ('queued','running')`); HNSW vector index; GIN index on a **generated** `tsvector` column so it cannot drift from the text; asyncpg connection pool (2–10); Supabase automated backups plus a manual snapshot before every migration; slow queries identified via `pg_stat_statements` |
| **Caching** | Redis for rate-limit windows, job dedupe keys and worker heartbeats; embedding cache keyed by content hash (`embedding_cache` table + in-process LRU); Anthropic prompt caching tracked via `cache_read_input_tokens`; Vercel CDN for frontend static assets; TTLs — JWKS 1 h, rate-limit windows 60 s/3600 s, signed storage URLs 5 min |
| **Infrastructure docs** | `docs/INFRASTRUCTURE.md`: full provisioning procedure, env-var checklist, smoke tests, rollback matrix, scaling notes. `scripts/migrate.py` and `scripts/seed.py` are idempotent and checksum-tracked |
| **Performance targets** | API p95 < 500 ms · DB p95 < 100 ms · uptime > 99.5% · error rate < 1% · 30-page analysis p95 < 90 s |

#### 2.2.4 Security & Costs

**Secrets management.** All secrets in environment variables, never in code.
`.env` is gitignored; `.env.example` documents every variable. Only
`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` and
`NEXT_PUBLIC_API_BASE_URL` reach the browser — the service-role key, JWT secret,
Anthropic key and embedding key are backend-only. Platform secret stores
(Vercel/Render/Railway environment settings) hold production values. Rotation
procedure: rotate at the provider, update the platform secret, redeploy, revoke
the old value; JWKS caching means Supabase key rotation needs no redeploy.
CI runs `gitleaks` on every PR and **fails the build** if any `.env` is
committed.

**Security hardening.**

| Control | Detail |
|---|---|
| Rate limiting | Redis sliding window, per organization: 20 analyses/hour, 200 requests/minute; `429` + `Retry-After`; fails **open** on Redis outage so the limiter cannot take down the API |
| Input validation | Pydantic v2 on every request body; magic-byte file sniffing (extension is secondary); 10 MB / 150-page budgets; encrypted-PDF rejection; DOCX zip-bomb guard (200:1 ratio); scanned-PDF detection by text density |
| Prompt-injection defence | Five layers — untrusted-data delimiters; no severity field with `extra="forbid"`; weights never in prompts; tools scoped to one document and rejecting cross-document IDs; deterministic scoring over two numbers |
| CORS | Explicit allowlist; **production startup fails** on `*` |
| HTTPS/SSL | Enforced by Vercel and Render/Railway; HSTS `max-age=31536000; includeSubDomains; preload` in production |
| Security headers | CSP (`frame-ancestors 'none'`, `object-src 'none'`), HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, COOP. A documentation-specific CSP applies to `/docs` and `/redoc` **outside production only** |
| AuthN/AuthZ | Supabase JWT verified server-side (ES256/RS256 via JWKS, legacy HS256); explicit algorithm allowlist; `alg=none` refused; issuer derived from `SUPABASE_URL`; org resolved from the database, never from a client-settable claim |
| Tenant isolation | Three independent layers — RLS, API ownership checks, route guards. Cross-tenant reads return `404` with a message identical to a genuinely missing resource |
| Audit | Append-only `audit_logs` with actor, action, resource, request ID, IP, safe metadata |

**Cost optimization.** Retrieve-don't-send (only top-K chunks per category, never
the whole contract); embedding cache by content hash; Anthropic prompt caching
with `cache_read_input_tokens` billed at the discounted rate; token counts
recorded per call in `usage_events`; per-analysis cost in
`analyses.estimated_cost_usd`; per-org analysis rate limit as a hard spend
ceiling; batched embedding requests (64/batch); usage page and admin metrics for
monitoring. Budget alerting is a nice-to-have (§2.2.1).

**Cost analysis (projected).**

Rates are those configured in `.env.example`
(`ANTHROPIC_INPUT_COST_PER_MTOK=3.00`, cached `0.30`, output `15.00`,
`EMBEDDING_COST_PER_MTOK=0.02`). **These are configuration values, not verified
quotes — confirm against current provider pricing before relying on them.**

*Per 30-page analysis, 12 categories:*

| Component | Volume | Rate | Cost |
|---|---|---|---|
| Embeddings (≈120 chunks × ~400 tok) | ~48 K tokens | $0.02 / M | ~$0.001 |
| Claude input (12 × ~4 K tok) | ~48 K tokens | $3.00 / M | ~$0.144 |
| Claude output (12 × ~800 tok) | ~9.6 K tokens | $15.00 / M | ~$0.144 |
| Executive summary | ~2 K in / 0.5 K out | — | ~$0.014 |
| **Total per analysis** | | | **≈ $0.30** |

Re-analysing the same document costs less (embeddings cached). Prompt caching
reduces repeat input cost by up to 10×. **Note: this projection exceeds the
$0.20 KPI in §2.2.1** — Week 2 includes measuring real cost and either tuning
`RETRIEVAL_TOP_K` / prompt size or revising the target to match reality.

*Monthly infrastructure, by scale:*

| Service | Free / dev tier | ~200 analyses/mo | ~2,000 analyses/mo |
|---|---|---|---|
| Supabase (Postgres, Auth, Storage) | $0 | $0–25 | $25 |
| Vercel (frontend) | $0 | $0 | $0–20 |
| Render/Railway API | $0–7 | $7 | $25 |
| Render/Railway worker | $0–7 | $7 | $25 |
| Upstash Redis | $0 | $0–10 | $10 |
| Sentry (optional) | $0 | $0 | $0–26 |
| **Infrastructure subtotal** | **$0–14** | **$14–49** | **$85–131** |
| Anthropic + embeddings @ ~$0.30 | — | ~$60 | ~$600 |
| **Total** | **~$0–14** | **~$75–110** | **~$685–730** |

For the course demo the expected spend is **under $15 total**: free tiers
throughout, plus a few dollars of Anthropic usage for evaluation runs and the
demo video.

**Security audit plan.**

| Activity | Tooling | Cadence |
|---|---|---|
| Secret scanning | `gitleaks` + committed-`.env` check (build-failing) | Every PR |
| Python SAST | `bandit -r app -ll` | Every PR |
| Python dependency advisories | `pip-audit` | Every PR |
| JS dependency advisories | `npm audit --audit-level=high` | Every PR |
| Auth/authz flow review | Manual, against `docs/SECURITY.md` | Week 2 |
| Tenant-isolation verification | Automated API tests + live two-tenant check | Week 2 |
| Findings and fixes | Recorded in `docs/SECURITY_AUDIT.md` with a residual-risk section | Week 2 |

`docs/SECURITY_AUDIT.md` documents 55 controls across 8 areas and — importantly
— an explicit residual-risk list rather than a clean bill of health.

---

## 3. Timeline & Milestones

Build phase: **two weeks remaining.** Week 1 = Aug 3–9 2026, Week 2 = Aug 10–16 2026.

### 3.1 Week 1 — Live infrastructure, real AI runs, measured evaluation

| Day | Goal | Deliverable |
|---|---|---|
| 1 | Provision Supabase; run migrations and seed against the real project | Schema live; default policy + 12 categories present |
| 1 | Obtain Anthropic and embedding API keys; provision Upstash Redis | `.env` complete; `/health/ready` green |
| 2 | First end-to-end analysis with real Claude calls | A completed analysis with verified findings |
| 2–3 | Fix whatever real provider behaviour exposes | Green `pytest`, lint, types |
| 3–4 | Expand the labelled evaluation set from 1 to 8–10 real agreements | `backend/evaluation/retrieval_labels.json` |
| 4 | Measure retrieval recall@8, verification pass rate, needs-review rate, cost/analysis, stage latency | `docs/EVALUATION.md` with **measured** numbers replacing "not measured" |
| 5 | Security audit pass; live two-tenant isolation check | `docs/SECURITY_AUDIT.md` updated |
| 5 | Commit discipline catch-up; push to `main`; confirm CI green | Green CI on GitHub |

**Week 1 exit criteria:** a real analysis completes end-to-end against live
Supabase, Redis and Claude; at least four KPIs carry measured values.

### 3.2 Week 2 — Deployment, documentation, demo, submission

| Day | Goal | Deliverable |
|---|---|---|
| 6 | Deploy API + worker (Render/Railway) from the Docker image | Public API with `/health` green |
| 6 | Deploy frontend (Vercel); wire CORS and Supabase redirect URLs | Public app URL |
| 7 | Run the 15-step smoke test in `docs/INFRASTRUCTURE.md` | All steps pass |
| 7 | Load/latency check; record API p95 and DB p95 | Performance KPIs measured |
| 8 | Tune cost per analysis toward the $0.20 target, or revise the target with evidence | Updated §2.2.4 |
| 8 | Fill README placeholders: Z-number, FAU email, deployed URL | README complete |
| 9 | Record and edit the 3–5 minute demo video | Video URL |
| 9 | Final docs pass: `plan.md`, `design.md`, `EVALUATION.md`, `IMPLEMENTATION_STATUS.md` | Docs consistent with reality |
| 10 | Final CI green; tag release; submit repository link on Canvas | Canvas submission |

**Week 2 exit criteria:** publicly accessible deployed app, demo video linked,
all documentation accurate, Canvas submission made.

### 3.3 Critical path and dependencies

```
Supabase project ──┬─> migrations ──> seed ──> real analysis ──> measured KPIs ──> demo video
                   │                              ▲                                    ▲
Anthropic key ─────┼──────────────────────────────┘                                    │
Embedding key ─────┤                                                                   │
Redis instance ────┘                                                                   │
                                                                                       │
Deployed API + worker ──> deployed frontend ──> smoke tests ─────────────────────────-─┘
```

**The critical path runs through credentials.** Nothing downstream — no real
analysis, no measured evaluation, no deployment, no demo video — can start until
the Supabase project, Anthropic key and embedding key exist. Obtaining them is
therefore Day 1, not Day 3.

| Dependency | Risk | Mitigation |
|---|---|---|
| Anthropic key requires billing | Blocks everything | Obtain Day 1; the entire test suite runs on doubles meanwhile |
| Supabase free-tier limits | Could throttle demo | Monitor; the demo needs only a handful of analyses |
| Real Claude behaviour differs from test doubles | Rework mid-week | Buffer on Days 2–3 explicitly reserved for this |
| Deployment platform quirks | Delays Week 2 | Deploy Day 6, not Day 9, leaving three days of slack |
| Labelled corpus takes longer than expected | Weak evaluation | 8–10 agreements is the floor; reduce count before reducing rigour |

### 3.4 Demo video plan (3–5 minutes)

| Time | Content |
|---|---|
| 0:00–0:30 | Problem: the clauses nobody reads; why fabricated findings are worse than none |
| 0:30–1:00 | Register, log in, dashboard with real data |
| 1:00–1:45 | Upload an agreement; show scanned-PDF rejection as a deliberate behaviour |
| 1:45–2:30 | Start analysis; real stage-by-stage progress; risk score and band |
| 2:30–3:30 | **The core demo:** click a finding → source pane scrolls and highlights the exact clause; open "How this severity was calculated"; show a quarantined finding and explain why it is excluded |
| 3:30–4:15 | Reviewer workflow: accept/dismiss/escalate/override; machine decision preserved. Policy editor: weights never sent to the model |
| 4:15–4:45 | Usage/cost page; admin metrics; prompt-injection resistance in one sentence |
| 4:45–5:00 | Not-legal-advice disclaimer; close |

### 3.5 Submission checklist

- [ ] `plan.md` and `design.md` on `main`
- [ ] README: name, Z-number, FAU email, deployed link, demo link, description, AI explanation, setup, tech stack
- [ ] Deployed app publicly reachable
- [ ] Demo video 3–5 min, linked
- [ ] Meaningful commit history on `main`; no secrets committed
- [ ] CI green
- [ ] Repository link submitted on Canvas

### 3.6 Buffer

Days 2–3 (provider-behaviour rework) and Days 9–10 (documentation and
submission) are deliberately light. If Week 1 slips, the labelled corpus shrinks
from 10 agreements to 5 before any deployment or documentation work is cut —
a smaller measured evaluation is acceptable, an unmeasured one is not.

---

## Related documents

| Document | Contents |
|---|---|
| [`design.md`](design.md) | Architecture, data flow, user flow, schema, API, AI components, deployment, decision rationale |
| [`docs/INFRASTRUCTURE.md`](docs/INFRASTRUCTURE.md) | Provisioning, env checklist, smoke tests, rollback |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model and control-by-control security model |
| [`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md) | 55-control audit and residual risk |
| [`docs/EVALUATION.md`](docs/EVALUATION.md) | Measured results, and what is explicitly not yet measured |
| [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) | Status, verification commands and results |

**Not legal advice.** ClauseGuard is an aid to human review, not a substitute
for a qualified lawyer.
