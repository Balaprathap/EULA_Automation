-- 0001: Extensions and table-independent helper functions.
-- Safe to re-run: every statement is idempotent.
--
-- ORDERING RULE: nothing in this file may reference an application table.
-- PostgreSQL validates the body of a LANGUAGE sql function at CREATE time
-- (check_function_bodies is on by default), so a helper that queries `profiles`
-- cannot be defined before `profiles` exists. The tenancy helpers that depend
-- on `profiles` therefore live at the end of migration 0002, immediately after
-- the table they read.
--
-- `set_updated_at` below is LANGUAGE plpgsql, whose body is NOT validated at
-- CREATE time and which references no table, so it is safe here.

-- ---------------------------------------------------------------------------
-- Extensions.
--
-- On Supabase, extensions are installed into the `extensions` schema rather
-- than `public`. Adding it to the search path here means the `vector` type
-- resolves whichever layout the target database uses.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'extensions') THEN
        EXECUTE 'SET search_path = public, extensions, pg_temp';
    END IF;
END
$$;

-- REQUIRED. pgvector powers similarity search; there is no fallback.
CREATE EXTENSION IF NOT EXISTS "vector";

-- OPTIONAL. PostgreSQL 13+ provides gen_random_uuid() natively, so pgcrypto is
-- only needed on older servers. Managed providers sometimes restrict it, and a
-- failure here should not block the whole schema.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS "pgcrypto";
EXCEPTION
    WHEN insufficient_privilege OR undefined_file OR feature_not_supported THEN
        RAISE NOTICE 'pgcrypto unavailable (%); relying on the built-in gen_random_uuid().', SQLERRM;
END
$$;

-- OPTIONAL. pg_trgm accelerates document-title search only. Without it the
-- application still works - the ILIKE search simply falls back to a sequential
-- scan. Migration 0003 creates the trigram index only if this succeeded.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS "pg_trgm";
EXCEPTION
    WHEN insufficient_privilege OR undefined_file OR feature_not_supported THEN
        RAISE NOTICE 'pg_trgm unavailable (%); the trigram title index will be skipped.', SQLERRM;
END
$$;

-- Fail fast and clearly if the one hard requirement is missing.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        RAISE EXCEPTION
            'The pgvector extension is required but is not installed. '
            'On Supabase enable it under Database > Extensions; locally use the '
            'pgvector/pgvector:pg16 image.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'gen_random_uuid') THEN
        RAISE EXCEPTION
            'gen_random_uuid() is unavailable. Install pgcrypto or use PostgreSQL 13 or newer.';
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;
