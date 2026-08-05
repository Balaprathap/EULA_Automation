# Evaluation

**Every number in this document is either a measurement from an actual run, or
is explicitly marked as not measured.** Nothing here is estimated and presented
as a result. Where a target has not been verified, it says so.

Last updated: 2026-08-02

---

## Measured

### Retrieval recall@k

Measured by `python -m scripts.evaluate_retrieval` against the labelled set in
`backend/evaluation/retrieval_labels.json`, using the deterministic offline
embedding provider.

| k | recall | categories found |
|---:|---:|---|
| 1 | 100.0% | 12 / 12 |
| 2 | 100.0% | 12 / 12 |
| 3 | 100.0% | 12 / 12 |
| 8 | 100.0% | 12 / 12 |

Target: `recall@8 >= 90%`. **Target met.**

**Read this number with appropriate scepticism.** It is a real measurement, but
the evaluation is small and easy:

- One labelled document (`backend/tests/fixtures/sample_eula.txt`), which
  produces **12 chunks**. Retrieving 8 of 12 is not a demanding task, which is
  why the table above also reports `recall@1` — that result is more meaningful.
- The sample agreement is cleanly structured, with one numbered section per
  policy category. Real agreements bury obligations in unrelated sections, use
  inconsistent terminology, and split single obligations across distant clauses.
- The run used the deterministic offline embedding provider. It is a hashed
  bag-of-words projection: texts sharing vocabulary land near each other, which
  is enough to exercise fusion and fallback logic, but it is not a real semantic
  embedding model. Production numbers with OpenAI or Voyage embeddings will
  differ, and have not been measured.
- The evaluation harness approximates PostgreSQL `ts_rank_cd` with term-overlap
  scoring rather than running against a live database.

To measure this properly, expand the labelled set to 20–30 real agreements from
different vendors and re-run against the configured production provider:

```bash
EMBEDDING_PROVIDER=openai EMBEDDING_API_KEY=... python -m scripts.evaluate_retrieval
```

### Fabricated quotes displayed

**0%.** This is a structural guarantee rather than a sampled statistic: a quote
that cannot be located in the cited chunk has no evidence row, therefore no
offsets, therefore nothing for the UI to display, and it is excluded from the
score.

Pinned by `tests/test_verification.py::TestFabricatedEvidence`, which asserts
that an invented quote is rejected, quarantined, produces no highlight offsets,
correctly updates the counts and verification pass rate, and leaves the rest of
the report usable. It covers both a wholly invented clause and a one-word
substitution (`may` → `must`).

Target: 0%. **Target met.**

### Automated test results

Command: `cd backend && ENVIRONMENT=test pytest -q`

```
356 passed
```

Command: `cd frontend && npm run test`

```
11 passed (1 file)
```

Static analysis, all run and all clean:

| Check | Command | Result |
|---|---|---|
| Backend lint | `ruff check app scripts tests` | All checks passed |
| Backend format | `ruff format --check app scripts tests` | 71 files already formatted |
| Backend types | `mypy app` | Success: no issues found in 53 source files |
| Frontend lint | `npx next lint` | No ESLint warnings or errors |
| Frontend types | `npx tsc --noEmit` | Exit 0 |
| Frontend build | `npx next build` | Compiled successfully, 13 routes |
| Migration syntax + dependency order | `pytest tests/test_migrations.py` | 71 passed — all 10 migrations parsed with libpg_query, plus catalog dependency-order checks |
| Migration execution | `scripts.migrate` on real PostgreSQL 16.2 + pgvector | 10 applied from zero, idempotent on re-run, seed verified |

### Prompt-injection resistance

20 tests in `tests/test_prompt_injection.py` run against an adversarial fixture
containing real injection strings. All pass. The tests assert:

- The system prompt states every boundary explicitly.
- The extraction schema has no severity field, and `extra="forbid"` rejects an
  injected one.
- The rendered prompt payload contains no severity weight, threshold, or
  escalation flag.
- `score_finding()` accepts exactly `{confidence, severity_weight, threshold,
  escalate, degraded_retrieval}` — no document, chunk, or text parameter exists.
- A tool call naming the document id from the injection fixture is **rejected**.
- An invented destructive tool name is refused.
- A document that successfully sways the model into abstaining produces an
  explicit `ABSTAINED` status, distinguishable from a clean pass.

---

## Not measured

These require a running stack with real provider credentials and a labelled
corpus of real agreements. **They have not been measured, and no value is
claimed for them.**

| Metric | Target | Status | What is needed |
|---|---|---|---|
| Clause recall | ≥ 90% | **Not measured** | A corpus of agreements with lawyer-annotated ground-truth clauses per category |
| Category precision | ≥ 85% | **Not measured** | The same corpus; measures how many reported findings are genuinely in-category |
| Verification pass rate | ≥ 95% | **Not measured** | Real Claude runs. The metric is computed and stored per analysis (`analyses.verification_pass_rate`) and surfaced on `/api/v1/admin/metrics`; it simply has no data yet |
| Needs-review rate | 5–15% | **Not measured** | Real runs. Recorded per category in `analysis_categories.status` |
| Reviewer override rate | < 20% | **Not measured** | Production use with real reviewers. Derivable from `finding_reviews` where `action = 'override_severity'` |
| Normal API p95 latency | < 500 ms | **Not measured** | A deployed API under load. Per-request duration is already logged |
| Database p95 latency | < 100 ms | **Not measured** | A live Postgres with representative data volume |
| 30-page analysis p95 | < 90 s | **Not measured** | Real Claude calls across 12 categories. Per-stage timings are recorded in `analyses.stage_timings_ms` and averaged on the admin metrics endpoint |
| Cache hit rate | — | **Not measured** | Embedding cache hits are counted per call (`EmbeddingResult.cache_hits`) and written to `usage_events.metadata` |
| Cost per analysis | — | **Not measured** | Computed and stored per analysis in `analyses.estimated_cost_usd` from provider-reported token counts |

**The instrumentation for every one of these already exists.** The gap is data,
not code — each metric has a column, an endpoint, or a log field waiting for a
real run to populate it.

---

## How to measure the rest

### Verification pass rate, needs-review rate, cost per analysis

Run analyses against real agreements with credentials configured, then:

```sql
SELECT
  AVG(verification_pass_rate)                                   AS avg_pass_rate,
  AVG(estimated_cost_usd)                                       AS avg_cost_usd,
  AVG(EXTRACT(EPOCH FROM (completed_at - started_at)))          AS avg_seconds,
  PERCENTILE_CONT(0.95) WITHIN GROUP (
    ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))
  )                                                             AS p95_seconds
FROM analyses
WHERE status IN ('complete', 'partial');

SELECT status, COUNT(*) * 100.0 / SUM(COUNT(*)) OVER () AS pct
FROM analysis_categories
GROUP BY status;
```

Or simply open `/api/v1/admin/metrics`, which computes all of this.

### Reviewer override rate

```sql
SELECT
  COUNT(*) FILTER (WHERE action = 'override_severity') * 100.0
    / NULLIF(COUNT(DISTINCT finding_id), 0) AS override_rate_pct
FROM finding_reviews;
```

### Clause recall and category precision

1. Collect 20–30 real agreements across different vendors and contract types.
2. Have a qualified reviewer annotate, for each policy category, every clause
   that genuinely belongs to it.
3. Run ClauseGuard against each agreement with production credentials.
4. Compare verified findings to the annotations:
   - **Clause recall** = annotated clauses found / annotated clauses total
   - **Category precision** = correctly-categorized findings / findings reported
5. Record the result here with the date, the model identifier, and the corpus
   size.

### API and database latency

Every request already logs `duration_ms` with a correlation id. With a deployed
API, aggregate from the log sink, or attach the optional Sentry integration
(`SENTRY_DSN`) for percentile tracking. Per-stage analysis timings are already
persisted in `analyses.stage_timings_ms`.

---

## Reproducing the measured results

```bash
cd backend
ENVIRONMENT=test pytest -q                                   # 339 tests
ENVIRONMENT=test EMBEDDING_PROVIDER=deterministic \
  python -m scripts.evaluate_retrieval                       # recall@8
ENVIRONMENT=test EMBEDDING_PROVIDER=deterministic \
  python -m scripts.evaluate_retrieval -k 1                  # recall@1

ruff check app scripts tests
ruff format --check app scripts tests
mypy app

cd ../frontend
npm ci && npm run lint && npm run typecheck && npm run test && npm run build
```
