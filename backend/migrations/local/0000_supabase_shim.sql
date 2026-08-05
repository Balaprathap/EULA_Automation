-- Local-development shim. NOT part of the numbered migration sequence and
-- NEVER applied to a real Supabase database.
--
-- Supabase provides the `auth` and `storage` schemas. The plain pgvector image
-- used by docker-compose does not, so the real migrations - which reference
-- auth.users, auth.uid(), storage.buckets, and storage.objects - would fail
-- against it.
--
-- This file creates the minimum compatible surface so the full schema can be
-- built and exercised locally. It is applied only by:
--     python -m scripts.migrate --local-shim
-- and the migrate script refuses to apply it to a *.supabase.co host.

CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS storage;

-- gen_random_uuid() is built in from PostgreSQL 13, so pgcrypto is optional.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS "pgcrypto";
EXCEPTION
    WHEN insufficient_privilege OR undefined_file OR feature_not_supported THEN
        RAISE NOTICE 'pgcrypto unavailable; using the built-in gen_random_uuid().';
END
$$;

-- Mirrors the columns our trigger reads from Supabase's auth.users.
CREATE TABLE IF NOT EXISTS auth.users (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email              TEXT UNIQUE,
    raw_user_meta_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- In Supabase this returns the current request's user id from the JWT. Locally
-- there is no request context, so it reads a session GUC that tests and manual
-- sessions can set:  SET LOCAL request.jwt.claim.sub = '<uuid>';
CREATE OR REPLACE FUNCTION auth.uid()
RETURNS UUID
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    RETURN NULLIF(current_setting('request.jwt.claim.sub', TRUE), '')::uuid;
EXCEPTION
    WHEN OTHERS THEN RETURN NULL;
END;
$$;

CREATE TABLE IF NOT EXISTS storage.buckets (
    id                 TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    public             BOOLEAN NOT NULL DEFAULT FALSE,
    file_size_limit    BIGINT,
    allowed_mime_types TEXT[],
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS storage.objects (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bucket_id  TEXT REFERENCES storage.buckets(id) ON DELETE CASCADE,
    name       TEXT,
    owner      UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;

-- Supabase splits an object key on '/' to give the folder path segments.
CREATE OR REPLACE FUNCTION storage.foldername(name TEXT)
RETURNS TEXT[]
LANGUAGE sql
IMMUTABLE
AS $$ SELECT string_to_array(name, '/') $$;
