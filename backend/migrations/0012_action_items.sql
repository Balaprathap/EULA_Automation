-- 0012: Action items derived from verified findings.
--
-- Purely additive and tenant-scoped. Nothing in an existing table changes.
--
-- Design note on dates: the schema has no contract start / effective /
-- renewal date, so a calendar due date CANNOT be derived from a clause such as
-- "ninety (90) days prior to renewal" without inventing an anchor. The
-- obligation and its duration are therefore stored, and `date_status` stays
-- 'unresolved' until a human supplies the date. `ai_due_date` is kept separate
-- from `due_date` so the original machine output is never overwritten.

CREATE TABLE IF NOT EXISTS action_items (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    analysis_id    UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    document_id    UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    -- Every action item MUST trace to a verified finding. NOT NULL is the
    -- structural guarantee that quarantined findings cannot produce one.
    finding_id     UUID NOT NULL REFERENCES findings(id) ON DELETE CASCADE,

    title          TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 300),
    description    TEXT NOT NULL,
    category       TEXT NOT NULL,
    obligation_type TEXT NOT NULL
                   CHECK (obligation_type IN (
                       'cancellation_deadline','automatic_renewal','notice_period',
                       'termination_requirement','payment_obligation','data_deletion',
                       'audit_requirement','data_retention','renewal_frequency',
                       'governing_law_followup','legal_escalation')),

    -- Verbatim, already verified against the source document.
    evidence_quote TEXT NOT NULL,
    doc_start_offset INTEGER,
    doc_end_offset   INTEGER,

    -- Machine-extracted duration, e.g. 90 days. NULL when none was stated.
    duration_days  INTEGER CHECK (duration_days IS NULL OR duration_days >= 0),
    duration_text  TEXT,

    -- Original machine output, never mutated by a human edit.
    ai_due_date    DATE,
    ai_priority    TEXT NOT NULL DEFAULT 'medium'
                   CHECK (ai_priority IN ('low','medium','high','urgent')),

    -- Human-editable fields.
    due_date       DATE,
    date_status    TEXT NOT NULL DEFAULT 'unresolved'
                   CHECK (date_status IN ('unresolved','ai_extracted','human_set','not_applicable')),
    assignee_id    UUID REFERENCES profiles(id) ON DELETE SET NULL,
    priority       TEXT NOT NULL DEFAULT 'medium'
                   CHECK (priority IN ('low','medium','high','urgent')),
    status         TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open','in_progress','completed','dismissed')),
    reviewer_note  TEXT CHECK (reviewer_note IS NULL OR length(reviewer_note) <= 4000),

    -- Regeneration key: stable across re-runs so duplicates are impossible.
    dedupe_key     TEXT NOT NULL,

    completed_at   TIMESTAMPTZ,
    completed_by   UUID REFERENCES profiles(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT action_item_dedupe_unique UNIQUE (analysis_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_action_items_org_status
    ON action_items(org_id, status, due_date NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_action_items_analysis ON action_items(analysis_id);
CREATE INDEX IF NOT EXISTS idx_action_items_document ON action_items(document_id);
CREATE INDEX IF NOT EXISTS idx_action_items_finding ON action_items(finding_id);
CREATE INDEX IF NOT EXISTS idx_action_items_assignee
    ON action_items(assignee_id) WHERE assignee_id IS NOT NULL;
-- Dashboard "urgent / overdue / due soon" widget hits exactly this shape.
CREATE INDEX IF NOT EXISTS idx_action_items_open_due
    ON action_items(org_id, due_date) WHERE status IN ('open','in_progress');

DROP TRIGGER IF EXISTS trg_action_items_updated ON action_items;
CREATE TRIGGER trg_action_items_updated BEFORE UPDATE ON action_items
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Human edit history. Append-only, mirroring finding_reviews: the machine's
-- original extraction is preserved and every change is attributable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS action_item_reviews (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    action_item_id UUID NOT NULL REFERENCES action_items(id) ON DELETE CASCADE,
    reviewer_id    UUID REFERENCES profiles(id) ON DELETE SET NULL,

    action         TEXT NOT NULL
                   CHECK (action IN ('status_change','due_date_set','assign',
                                     'priority_change','note','dismiss','complete')),
    field_changed  TEXT,
    previous_value TEXT,
    new_value      TEXT,
    note           TEXT CHECK (note IS NULL OR length(note) <= 4000),

    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_action_item_reviews_item
    ON action_item_reviews(action_item_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_item_reviews_org ON action_item_reviews(org_id);

-- ---------------------------------------------------------------------------
-- Row-Level Security, matching the pattern used by every other tenant table.
-- ---------------------------------------------------------------------------
ALTER TABLE action_items        ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_item_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_items        FORCE ROW LEVEL SECURITY;
ALTER TABLE action_item_reviews FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS action_items_select ON action_items;
CREATE POLICY action_items_select ON action_items
    FOR SELECT USING (
        org_id = auth_org_id()
        AND EXISTS (
            SELECT 1 FROM analyses a
            WHERE a.id = action_items.analysis_id AND a.org_id = auth_org_id()
        )
    );

DROP POLICY IF EXISTS action_items_update ON action_items;
CREATE POLICY action_items_update ON action_items
    FOR UPDATE USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());

DROP POLICY IF EXISTS action_item_reviews_select ON action_item_reviews;
CREATE POLICY action_item_reviews_select ON action_item_reviews
    FOR SELECT USING (org_id = auth_org_id());

DROP POLICY IF EXISTS action_item_reviews_insert ON action_item_reviews;
CREATE POLICY action_item_reviews_insert ON action_item_reviews
    FOR INSERT WITH CHECK (
        org_id = auth_org_id()
        AND reviewer_id = auth.uid()
        AND EXISTS (
            SELECT 1 FROM action_items i
            WHERE i.id = action_item_reviews.action_item_id AND i.org_id = auth_org_id()
        )
    );
-- No UPDATE or DELETE policy: the edit history is append-only at the database
-- level, exactly as finding_reviews is.
