-- 0007: Audit log and usage/cost accounting. Both are append-only.

CREATE TABLE IF NOT EXISTS audit_logs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    actor_id      UUID REFERENCES profiles(id) ON DELETE SET NULL,
    actor_email   TEXT,

    action        TEXT NOT NULL CHECK (length(action) BETWEEN 1 AND 100),
    resource_type TEXT NOT NULL,
    resource_id   UUID,

    request_id    TEXT,
    ip_address    INET,
    user_agent    TEXT,
    -- Safe metadata only. Never document text, quotes, tokens, or keys.
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_org_created ON audit_logs(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_id, created_at DESC);

CREATE TABLE IF NOT EXISTS usage_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    analysis_id   UUID REFERENCES analyses(id) ON DELETE SET NULL,
    actor_id      UUID REFERENCES profiles(id) ON DELETE SET NULL,

    event_type    TEXT NOT NULL
                  CHECK (event_type IN ('llm_extraction','llm_summary','embedding',
                                        'document_upload','analysis_run')),
    provider      TEXT,
    model         TEXT,

    input_tokens        INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    cached_input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (cached_input_tokens >= 0),
    output_tokens       INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    estimated_cost_usd  NUMERIC(12,6) NOT NULL DEFAULT 0 CHECK (estimated_cost_usd >= 0),

    duration_ms   NUMERIC(10,2),
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_usage_org_created ON usage_events(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_analysis ON usage_events(analysis_id);
CREATE INDEX IF NOT EXISTS idx_usage_org_type_created
    ON usage_events(org_id, event_type, created_at DESC);

-- Schema bookkeeping for scripts/migrate.py.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum   TEXT
);
