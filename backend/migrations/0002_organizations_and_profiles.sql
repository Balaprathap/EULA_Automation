-- 0002: Tenancy root, plus the helper functions that read it.
--
-- Every other table hangs off organizations. The auth_* helpers at the bottom
-- of this file are defined HERE rather than in 0001 because they query
-- `profiles`, and PostgreSQL validates LANGUAGE sql function bodies at CREATE
-- time - defining them earlier fails with
--     UndefinedTableError: relation "profiles" does not exist

CREATE TABLE IF NOT EXISTS organizations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
    slug        TEXT UNIQUE NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    plan        TEXT NOT NULL DEFAULT 'free'
                CHECK (plan IN ('free', 'team', 'enterprise')),
    is_demo     BOOLEAN NOT NULL DEFAULT FALSE,
    settings    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Mirrors auth.users, adding tenancy and role. One profile per Supabase user.
CREATE TABLE IF NOT EXISTS profiles (
    id           UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    org_id       UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    full_name    TEXT,
    role         TEXT NOT NULL DEFAULT 'member'
                 CHECK (role IN ('owner', 'admin', 'member')),
    avatar_url   TEXT,
    last_seen_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_profiles_org ON profiles(org_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_email_lower ON profiles(lower(email));

-- Exactly one owner per organization.
CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_single_owner
    ON profiles(org_id) WHERE role = 'owner';

DROP TRIGGER IF EXISTS trg_organizations_updated ON organizations;
CREATE TRIGGER trg_organizations_updated BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_profiles_updated ON profiles;
CREATE TRIGGER trg_profiles_updated BEFORE UPDATE ON profiles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Tenancy helpers.
--
-- Every RLS policy in migration 0008 routes through these, so organization
-- scoping is defined in exactly one place. SECURITY DEFINER lets them read
-- `profiles` regardless of the caller's own RLS, and the pinned search_path
-- stops the functions being shadowed by a malicious schema on the caller's
-- search path.
--
-- These MUST stay after the profiles table above - see the header comment.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION auth_org_id()
RETURNS UUID
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT org_id FROM profiles WHERE id = auth.uid();
$$;

CREATE OR REPLACE FUNCTION auth_role()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT role FROM profiles WHERE id = auth.uid();
$$;

CREATE OR REPLACE FUNCTION auth_is_admin()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT COALESCE(
        (SELECT role IN ('owner', 'admin') FROM profiles WHERE id = auth.uid()),
        FALSE
    );
$$;
