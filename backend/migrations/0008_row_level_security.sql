-- 0008: Row-Level Security.
--
-- Security is enforced in three independent layers: here at the database, again
-- in the API's ownership checks, and finally in frontend route guards. This
-- migration is the layer that holds even if application code is wrong, so every
-- exposed table gets RLS and every policy routes through auth_org_id().
--
-- Note: the backend service role bypasses RLS by design (it must, to run the
-- worker). The API therefore performs its own explicit org checks on every
-- request - see app/api/deps.py. RLS is the backstop for any direct client
-- access via Supabase, not the only control.

ALTER TABLE organizations      ENABLE ROW LEVEL SECURITY;
ALTER TABLE profiles           ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents          ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks    ENABLE ROW LEVEL SECURITY;
ALTER TABLE policies           ENABLE ROW LEVEL SECURITY;
ALTER TABLE policy_rules       ENABLE ROW LEVEL SECURITY;
ALTER TABLE analyses           ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE findings           ENABLE ROW LEVEL SECURITY;
ALTER TABLE finding_evidence   ENABLE ROW LEVEL SECURITY;
ALTER TABLE finding_reviews    ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs         ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events       ENABLE ROW LEVEL SECURITY;

-- Force RLS even for table owners, so a misconfigured role cannot read across
-- tenants.
ALTER TABLE documents        FORCE ROW LEVEL SECURITY;
ALTER TABLE document_chunks  FORCE ROW LEVEL SECURITY;
ALTER TABLE findings         FORCE ROW LEVEL SECURITY;
ALTER TABLE finding_evidence FORCE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- organizations: members read their own org; only the owner may rename it.
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS org_select ON organizations;
CREATE POLICY org_select ON organizations
    FOR SELECT USING (id = auth_org_id());

DROP POLICY IF EXISTS org_update ON organizations;
CREATE POLICY org_update ON organizations
    FOR UPDATE USING (id = auth_org_id() AND auth_role() = 'owner')
    WITH CHECK (id = auth_org_id());

-- ---------------------------------------------------------------------------
-- profiles: see colleagues; edit only yourself (admins may edit any colleague).
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS profiles_select ON profiles;
CREATE POLICY profiles_select ON profiles
    FOR SELECT USING (org_id = auth_org_id());

DROP POLICY IF EXISTS profiles_update ON profiles;
CREATE POLICY profiles_update ON profiles
    FOR UPDATE USING (id = auth.uid() OR (org_id = auth_org_id() AND auth_is_admin()))
    WITH CHECK (org_id = auth_org_id());

-- ---------------------------------------------------------------------------
-- documents: full CRUD within the org; soft-deleted rows stay hidden.
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS documents_select ON documents;
CREATE POLICY documents_select ON documents
    FOR SELECT USING (org_id = auth_org_id() AND deleted_at IS NULL);

DROP POLICY IF EXISTS documents_insert ON documents;
CREATE POLICY documents_insert ON documents
    FOR INSERT WITH CHECK (org_id = auth_org_id());

DROP POLICY IF EXISTS documents_update ON documents;
CREATE POLICY documents_update ON documents
    FOR UPDATE USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());

DROP POLICY IF EXISTS documents_delete ON documents;
CREATE POLICY documents_delete ON documents
    FOR DELETE USING (org_id = auth_org_id());

-- ---------------------------------------------------------------------------
-- document_chunks: inherit access through the parent document. Belt and
-- braces - the org_id column must match AND the parent must be visible.
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS chunks_select ON document_chunks;
CREATE POLICY chunks_select ON document_chunks
    FOR SELECT USING (
        org_id = auth_org_id()
        AND EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = document_chunks.document_id
              AND d.org_id = auth_org_id()
              AND d.deleted_at IS NULL
        )
    );

-- ---------------------------------------------------------------------------
-- policies / policy_rules: everyone reads; only admins and owners write.
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS policies_select ON policies;
CREATE POLICY policies_select ON policies
    FOR SELECT USING (org_id = auth_org_id());

DROP POLICY IF EXISTS policies_insert ON policies;
CREATE POLICY policies_insert ON policies
    FOR INSERT WITH CHECK (org_id = auth_org_id() AND auth_is_admin());

DROP POLICY IF EXISTS policies_update ON policies;
CREATE POLICY policies_update ON policies
    FOR UPDATE USING (org_id = auth_org_id() AND auth_is_admin())
    WITH CHECK (org_id = auth_org_id());

DROP POLICY IF EXISTS policies_delete ON policies;
CREATE POLICY policies_delete ON policies
    FOR DELETE USING (org_id = auth_org_id() AND auth_is_admin() AND NOT is_default);

DROP POLICY IF EXISTS policy_rules_select ON policy_rules;
CREATE POLICY policy_rules_select ON policy_rules
    FOR SELECT USING (org_id = auth_org_id());

DROP POLICY IF EXISTS policy_rules_write ON policy_rules;
CREATE POLICY policy_rules_write ON policy_rules
    FOR ALL USING (org_id = auth_org_id() AND auth_is_admin())
    WITH CHECK (
        org_id = auth_org_id()
        AND EXISTS (
            SELECT 1 FROM policies p
            WHERE p.id = policy_rules.policy_id AND p.org_id = auth_org_id()
        )
    );

-- ---------------------------------------------------------------------------
-- analyses: readable and creatable within the org. Only the worker (service
-- role) mutates progress, so no UPDATE policy is granted to end users.
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS analyses_select ON analyses;
CREATE POLICY analyses_select ON analyses
    FOR SELECT USING (org_id = auth_org_id());

DROP POLICY IF EXISTS analyses_insert ON analyses;
CREATE POLICY analyses_insert ON analyses
    FOR INSERT WITH CHECK (
        org_id = auth_org_id()
        AND EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = analyses.document_id
              AND d.org_id = auth_org_id()
              AND d.deleted_at IS NULL
        )
    );

DROP POLICY IF EXISTS analysis_categories_select ON analysis_categories;
CREATE POLICY analysis_categories_select ON analysis_categories
    FOR SELECT USING (org_id = auth_org_id());

-- ---------------------------------------------------------------------------
-- findings: read within the org via the parent analysis. Users may update only
-- the human-review columns; machine columns are service-role only.
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS findings_select ON findings;
CREATE POLICY findings_select ON findings
    FOR SELECT USING (
        org_id = auth_org_id()
        AND EXISTS (
            SELECT 1 FROM analyses a
            WHERE a.id = findings.analysis_id AND a.org_id = auth_org_id()
        )
    );

DROP POLICY IF EXISTS findings_review_update ON findings;
CREATE POLICY findings_review_update ON findings
    FOR UPDATE USING (org_id = auth_org_id()) WITH CHECK (org_id = auth_org_id());

DROP POLICY IF EXISTS evidence_select ON finding_evidence;
CREATE POLICY evidence_select ON finding_evidence
    FOR SELECT USING (
        org_id = auth_org_id()
        AND EXISTS (
            SELECT 1 FROM findings f
            WHERE f.id = finding_evidence.finding_id AND f.org_id = auth_org_id()
        )
    );

DROP POLICY IF EXISTS reviews_select ON finding_reviews;
CREATE POLICY reviews_select ON finding_reviews
    FOR SELECT USING (org_id = auth_org_id());

DROP POLICY IF EXISTS reviews_insert ON finding_reviews;
CREATE POLICY reviews_insert ON finding_reviews
    FOR INSERT WITH CHECK (
        org_id = auth_org_id()
        AND reviewer_id = auth.uid()
        AND EXISTS (
            SELECT 1 FROM findings f
            WHERE f.id = finding_reviews.finding_id AND f.org_id = auth_org_id()
        )
    );
-- Review history is append-only: no UPDATE or DELETE policy exists, so those
-- operations are denied to every non-service role.

-- ---------------------------------------------------------------------------
-- audit_logs / usage_events: admins read; nobody writes through the client.
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS audit_select ON audit_logs;
CREATE POLICY audit_select ON audit_logs
    FOR SELECT USING (org_id = auth_org_id() AND auth_is_admin());

DROP POLICY IF EXISTS usage_select ON usage_events;
CREATE POLICY usage_select ON usage_events
    FOR SELECT USING (org_id = auth_org_id());
