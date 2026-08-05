-- 0005: Analysis runs and their resumable per-stage progress.

CREATE TABLE IF NOT EXISTS analyses (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    document_id    UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    policy_id      UUID NOT NULL REFERENCES policies(id) ON DELETE RESTRICT,
    requested_by   UUID REFERENCES profiles(id) ON DELETE SET NULL,

    status         TEXT NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'running', 'complete', 'partial', 'failed', 'cancelled')),
    stage          TEXT NOT NULL DEFAULT 'queued'
                   CHECK (stage IN ('queued', 'parsing', 'chunking', 'retrieving',
                                    'extracting', 'verifying', 'scoring', 'complete', 'failed')),
    progress_message TEXT,

    categories_total     INTEGER NOT NULL DEFAULT 0 CHECK (categories_total >= 0),
    categories_completed INTEGER NOT NULL DEFAULT 0 CHECK (categories_completed >= 0),
    -- Resumability: a restarted worker skips whatever is already listed here.
    completed_categories TEXT[] NOT NULL DEFAULT '{}',

    -- Deterministic scoring outputs.
    overall_score   NUMERIC(5,2) CHECK (overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100)),
    risk_band       TEXT CHECK (risk_band IS NULL OR risk_band IN ('low', 'moderate', 'elevated', 'high')),
    finding_count   INTEGER NOT NULL DEFAULT 0 CHECK (finding_count >= 0),
    review_count    INTEGER NOT NULL DEFAULT 0 CHECK (review_count >= 0),
    quarantine_count INTEGER NOT NULL DEFAULT 0 CHECK (quarantine_count >= 0),
    verification_pass_rate NUMERIC(5,2)
                   CHECK (verification_pass_rate IS NULL
                          OR (verification_pass_rate >= 0 AND verification_pass_rate <= 100)),
    executive_summary TEXT,

    degraded_retrieval BOOLEAN NOT NULL DEFAULT FALSE,
    degraded_reason    TEXT,

    -- Observability.
    stage_timings_ms JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_tokens     BIGINT NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    cached_input_tokens BIGINT NOT NULL DEFAULT 0 CHECK (cached_input_tokens >= 0),
    output_tokens    BIGINT NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    estimated_cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0 CHECK (estimated_cost_usd >= 0),
    model_used       TEXT,

    error_code       TEXT,
    error_message    TEXT,
    attempt_count    INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),

    -- Worker liveness: a heartbeat that stops advancing means a dead worker.
    worker_id        TEXT,
    heartbeat_at     TIMESTAMPTZ,

    -- Idempotency key: prevents a double-clicked "Analyze" creating two runs.
    idempotency_key  TEXT,

    queued_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analyses_org_created ON analyses(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analyses_document ON analyses(document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analyses_status ON analyses(status)
    WHERE status IN ('queued', 'running');
-- Duplicate-job prevention: one live analysis per document+policy at a time.
CREATE UNIQUE INDEX IF NOT EXISTS idx_analyses_active_unique
    ON analyses(document_id, policy_id)
    WHERE status IN ('queued', 'running');
CREATE UNIQUE INDEX IF NOT EXISTS idx_analyses_idempotency
    ON analyses(org_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
-- Stalled-run detection for the worker recovery sweep.
CREATE INDEX IF NOT EXISTS idx_analyses_heartbeat
    ON analyses(heartbeat_at) WHERE status = 'running';

DROP TRIGGER IF EXISTS trg_analyses_updated ON analyses;
CREATE TRIGGER trg_analyses_updated BEFORE UPDATE ON analyses
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Per-category outcome, so a partial report can name exactly what is missing.
CREATE TABLE IF NOT EXISTS analysis_categories (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    analysis_id   UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    category      TEXT NOT NULL,
    status        TEXT NOT NULL
                  CHECK (status IN ('pending', 'completed', 'abstained', 'needs_review', 'failed')),
    needs_review_reason TEXT,
    error_code    TEXT,
    retrieval_mode TEXT CHECK (retrieval_mode IS NULL
                   OR retrieval_mode IN ('hybrid', 'dense', 'keyword', 'ordinal_scan')),
    degraded_reason TEXT,
    tool_calls    INTEGER NOT NULL DEFAULT 0 CHECK (tool_calls >= 0),
    duration_ms   NUMERIC(10,2),
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT analysis_category_unique UNIQUE (analysis_id, category)
);

CREATE INDEX IF NOT EXISTS idx_analysis_categories_analysis
    ON analysis_categories(analysis_id);
