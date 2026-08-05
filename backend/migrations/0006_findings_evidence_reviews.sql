-- 0006: Findings, verified evidence, and the human review trail.
--
-- Machine decisions and human decisions are stored separately and permanently.
-- A reviewer override sets override_severity and appends a review record; the
-- original machine_severity is never mutated.

CREATE TABLE IF NOT EXISTS findings (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    analysis_id    UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    document_id    UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    policy_rule_id UUID REFERENCES policy_rules(id) ON DELETE SET NULL,
    chunk_id       UUID REFERENCES document_chunks(id) ON DELETE SET NULL,

    category       TEXT NOT NULL,
    plain_summary  TEXT NOT NULL,
    why_it_matters TEXT NOT NULL,

    -- Scoring inputs, persisted so any score can be re-derived and audited.
    model_confidence NUMERIC(4,3) NOT NULL
                     CHECK (model_confidence >= 0 AND model_confidence <= 1),
    severity_weight  NUMERIC(3,2) NOT NULL
                     CHECK (severity_weight >= 0 AND severity_weight <= 1),
    confidence_threshold NUMERIC(3,2) NOT NULL DEFAULT 0.35,
    weighted_risk    NUMERIC(4,3) NOT NULL DEFAULT 0,

    -- Machine decision. Immutable once written.
    machine_severity TEXT NOT NULL
                     CHECK (machine_severity IN ('info','low','medium','high','critical')),
    severity_source  TEXT NOT NULL DEFAULT 'deterministic'
                     CHECK (severity_source IN ('deterministic', 'human_override', 'degraded_cap')),
    scoring_explanation TEXT NOT NULL,

    -- Human decision. NULL until a reviewer acts.
    override_severity TEXT
                     CHECK (override_severity IS NULL
                            OR override_severity IN ('info','low','medium','high','critical')),
    review_status    TEXT NOT NULL DEFAULT 'pending'
                     CHECK (review_status IN ('pending','accepted','dismissed','escalated')),
    reviewed_by      UUID REFERENCES profiles(id) ON DELETE SET NULL,
    reviewed_at      TIMESTAMPTZ,

    verification_status TEXT NOT NULL DEFAULT 'pending'
                     CHECK (verification_status IN ('pending','verified','quarantined','needs_review')),
    quarantine_reason TEXT,
    degraded_retrieval BOOLEAN NOT NULL DEFAULT FALSE,

    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_findings_analysis ON findings(analysis_id);
CREATE INDEX IF NOT EXISTS idx_findings_org ON findings(org_id);
CREATE INDEX IF NOT EXISTS idx_findings_document ON findings(document_id);
CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(analysis_id, category);
-- The workspace's default view: verified findings, most severe first.
CREATE INDEX IF NOT EXISTS idx_findings_verified
    ON findings(analysis_id, machine_severity)
    WHERE verification_status = 'verified';
CREATE INDEX IF NOT EXISTS idx_findings_pending_review
    ON findings(org_id, review_status) WHERE review_status = 'pending';

DROP TRIGGER IF EXISTS trg_findings_updated ON findings;
CREATE TRIGGER trg_findings_updated BEFORE UPDATE ON findings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Evidence. A row here exists only after verification succeeded.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS finding_evidence (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    finding_id    UUID NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    chunk_id      UUID REFERENCES document_chunks(id) ON DELETE SET NULL,
    document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    quote         TEXT NOT NULL CHECK (length(quote) > 0),
    -- Absolute offsets into documents.normalized_text; drive UI highlighting.
    doc_start_offset INTEGER NOT NULL CHECK (doc_start_offset >= 0),
    doc_end_offset   INTEGER NOT NULL,
    chunk_start_offset INTEGER CHECK (chunk_start_offset IS NULL OR chunk_start_offset >= 0),
    chunk_end_offset   INTEGER,

    verification_method TEXT NOT NULL
                  CHECK (verification_method IN ('offset_exact', 'offset_normalized')),
    verified_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT evidence_span_valid CHECK (doc_end_offset > doc_start_offset)
);

CREATE INDEX IF NOT EXISTS idx_evidence_finding ON finding_evidence(finding_id);
CREATE INDEX IF NOT EXISTS idx_evidence_document ON finding_evidence(document_id);

-- ---------------------------------------------------------------------------
-- Review history. Append-only; nothing here is ever updated or deleted.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS finding_reviews (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    finding_id        UUID NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    reviewer_id       UUID REFERENCES profiles(id) ON DELETE SET NULL,

    action            TEXT NOT NULL
                      CHECK (action IN ('accept','dismiss','escalate','override_severity','note')),
    -- Snapshot of what the machine said, preserved independently of findings.
    previous_severity TEXT,
    new_severity      TEXT
                      CHECK (new_severity IS NULL
                             OR new_severity IN ('info','low','medium','high','critical')),
    previous_status   TEXT,
    new_status        TEXT,
    note              TEXT CHECK (note IS NULL OR length(note) <= 4000),
    reason            TEXT,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reviews_finding ON finding_reviews(finding_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reviews_org ON finding_reviews(org_id, created_at DESC);
