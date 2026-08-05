-- 0004: Compliance policies and their per-category rules.
--
-- Severity weights and thresholds live here and NEVER travel to the model.
-- This is the operator-controlled half of deterministic scoring.

CREATE TABLE IF NOT EXISTS policies (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    created_by  UUID REFERENCES profiles(id) ON DELETE SET NULL,

    name        TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
    description TEXT,
    version     INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    is_default  BOOLEAN NOT NULL DEFAULT FALSE,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT policy_name_version_unique UNIQUE (org_id, name, version)
);

-- At most one default policy per organization.
CREATE UNIQUE INDEX IF NOT EXISTS idx_policies_single_default
    ON policies(org_id) WHERE is_default;
CREATE INDEX IF NOT EXISTS idx_policies_org_active
    ON policies(org_id, is_active);

DROP TRIGGER IF EXISTS trg_policies_updated ON policies;
CREATE TRIGGER trg_policies_updated BEFORE UPDATE ON policies
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS policy_rules (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    policy_id          UUID NOT NULL REFERENCES policies(id) ON DELETE CASCADE,

    category           TEXT NOT NULL CHECK (category ~ '^[a-z][a-z0-9_]{1,63}$'),
    display_name       TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 120),
    description        TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 2000),
    retrieval_guidance TEXT,
    keywords           TEXT[] NOT NULL DEFAULT '{}',

    -- Deterministic scoring inputs. Never sent to the model.
    severity_weight    NUMERIC(3,2) NOT NULL DEFAULT 0.50
                       CHECK (severity_weight >= 0 AND severity_weight <= 1),
    confidence_threshold NUMERIC(3,2) NOT NULL DEFAULT 0.35
                       CHECK (confidence_threshold >= 0 AND confidence_threshold <= 1),
    escalate           BOOLEAN NOT NULL DEFAULT FALSE,

    is_enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order         INTEGER NOT NULL DEFAULT 0,

    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT policy_rule_category_unique UNIQUE (policy_id, category)
);

CREATE INDEX IF NOT EXISTS idx_policy_rules_policy
    ON policy_rules(policy_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_policy_rules_org ON policy_rules(org_id);

DROP TRIGGER IF EXISTS trg_policy_rules_updated ON policy_rules;
CREATE TRIGGER trg_policy_rules_updated BEFORE UPDATE ON policy_rules
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
