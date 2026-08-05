-- 0011: PDF report generation and email delivery.
--
-- Purely additive. No existing table, column, or constraint is modified.
-- Analysis status remains entirely independent of report or email state: a
-- failed email must never make a completed analysis look failed.

CREATE TABLE IF NOT EXISTS analysis_reports (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    analysis_id       UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,

    storage_path      TEXT,
    version           INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    generation_status TEXT NOT NULL DEFAULT 'pending'
                      CHECK (generation_status IN ('pending','generating','ready','failed')),
    generated_at      TIMESTAMPTZ,
    file_size         BIGINT CHECK (file_size IS NULL OR file_size >= 0),
    checksum          TEXT CHECK (checksum IS NULL OR checksum ~ '^[a-f0-9]{64}$'),
    error_code        TEXT,
    error_message     TEXT,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One report row per analysis per version. A worker retry re-uses the row
    -- rather than creating a second report.
    CONSTRAINT analysis_report_version_unique UNIQUE (analysis_id, version)
);

CREATE INDEX IF NOT EXISTS idx_reports_org ON analysis_reports(org_id);
CREATE INDEX IF NOT EXISTS idx_reports_analysis ON analysis_reports(analysis_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_reports_ready
    ON analysis_reports(analysis_id) WHERE generation_status = 'ready';

DROP TRIGGER IF EXISTS trg_analysis_reports_updated ON analysis_reports;
CREATE TRIGGER trg_analysis_reports_updated BEFORE UPDATE ON analysis_reports
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Email delivery attempts.
--
-- The recipient is always resolved server-side from the authenticated Supabase
-- profile. The address is stored hashed so the audit trail survives without
-- keeping a plaintext mailbox in a second place; a masked form is kept for
-- display only.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS report_deliveries (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    report_id           UUID NOT NULL REFERENCES analysis_reports(id) ON DELETE CASCADE,
    analysis_id         UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,

    recipient_user_id   UUID REFERENCES profiles(id) ON DELETE SET NULL,
    recipient_email_hash TEXT NOT NULL CHECK (recipient_email_hash ~ '^[a-f0-9]{64}$'),
    recipient_masked    TEXT NOT NULL,

    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','sending','sent','failed','permanently_failed')),
    attempt_count       INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    provider            TEXT,
    provider_message_id TEXT,
    error_code          TEXT,
    error_message_safe  TEXT,
    delivery_mode       TEXT CHECK (delivery_mode IS NULL OR delivery_mode IN ('attachment','link')),

    sent_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deliveries_org ON report_deliveries(org_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_analysis ON report_deliveries(analysis_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deliveries_report ON report_deliveries(report_id);

-- Duplicate-send protection. A worker retry, or a double-clicked resend, cannot
-- produce a second automatic email for the same analysis + recipient + version:
-- the row already exists in a non-failed state and the INSERT is a no-op.
CREATE UNIQUE INDEX IF NOT EXISTS idx_deliveries_idempotent
    ON report_deliveries(analysis_id, report_id, recipient_email_hash)
    WHERE status IN ('pending', 'sending', 'sent');

DROP TRIGGER IF EXISTS trg_report_deliveries_updated ON report_deliveries;
CREATE TRIGGER trg_report_deliveries_updated BEFORE UPDATE ON report_deliveries
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Row-Level Security, matching the pattern used by every other tenant table.
-- Reports are written by the worker (service role); users may only read.
-- ---------------------------------------------------------------------------
ALTER TABLE analysis_reports  ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_reports  FORCE ROW LEVEL SECURITY;
ALTER TABLE report_deliveries FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS analysis_reports_select ON analysis_reports;
CREATE POLICY analysis_reports_select ON analysis_reports
    FOR SELECT USING (
        org_id = auth_org_id()
        AND EXISTS (
            SELECT 1 FROM analyses a
            WHERE a.id = analysis_reports.analysis_id AND a.org_id = auth_org_id()
        )
    );

DROP POLICY IF EXISTS report_deliveries_select ON report_deliveries;
CREATE POLICY report_deliveries_select ON report_deliveries
    FOR SELECT USING (
        org_id = auth_org_id()
        AND EXISTS (
            SELECT 1 FROM analysis_reports r
            WHERE r.id = report_deliveries.report_id AND r.org_id = auth_org_id()
        )
    );

-- ---------------------------------------------------------------------------
-- Private storage bucket for generated reports.
--
-- Keys are {org_id}/{analysis_id}/report-v{n}.pdf. The org id must be the FIRST
-- path segment because that is what the storage policy checks - the same
-- convention migration 0009 uses for source documents.
-- ---------------------------------------------------------------------------
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('reports', 'reports', FALSE, 26214400, ARRAY['application/pdf'])
ON CONFLICT (id) DO UPDATE
    SET public = FALSE,
        file_size_limit = EXCLUDED.file_size_limit,
        allowed_mime_types = EXCLUDED.allowed_mime_types;

DROP POLICY IF EXISTS reports_storage_select ON storage.objects;
CREATE POLICY reports_storage_select ON storage.objects
    FOR SELECT USING (
        bucket_id = 'reports'
        AND (storage.foldername(name))[1] = auth_org_id()::text
    );
