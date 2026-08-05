-- 0003: Documents, their normalized text, and clause-aware chunks.
--
-- The offsets stored here index into documents.normalized_text and are the
-- authoritative coordinate space for evidence verification and UI highlighting.

CREATE TABLE IF NOT EXISTS documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    uploaded_by     UUID REFERENCES profiles(id) ON DELETE SET NULL,

    title           TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 500),
    vendor_name     TEXT CHECK (vendor_name IS NULL OR length(vendor_name) <= 200),

    source_type     TEXT NOT NULL CHECK (source_type IN ('pdf', 'docx', 'txt', 'paste')),
    storage_path    TEXT,                       -- private bucket key; NULL for pasted text
    original_filename TEXT,
    file_size_bytes BIGINT CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),

    normalized_text TEXT,
    content_sha256  TEXT CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[a-f0-9]{64}$'),
    page_count      INTEGER CHECK (page_count IS NULL OR page_count >= 0),
    char_count      INTEGER CHECK (char_count IS NULL OR char_count >= 0),

    status          TEXT NOT NULL DEFAULT 'uploaded'
                    CHECK (status IN ('uploaded', 'parsing', 'chunking', 'ready', 'failed')),
    error_code      TEXT,
    error_message   TEXT,

    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    deleted_at      TIMESTAMPTZ,                -- soft delete
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_org_created
    ON documents(org_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_documents_org_status
    ON documents(org_id, status) WHERE deleted_at IS NULL;
-- Trigram index for title search. Created only when pg_trgm is present -
-- see the optional extension block in migration 0001. Without it, title
-- search still works via a sequential ILIKE scan.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        CREATE INDEX IF NOT EXISTS idx_documents_title_trgm
            ON documents USING gin (title gin_trgm_ops);
    ELSE
        RAISE NOTICE 'pg_trgm not installed; skipping idx_documents_title_trgm.';
    END IF;
END
$$;
-- Same content uploaded twice within an org is detectable, not forbidden.
CREATE INDEX IF NOT EXISTS idx_documents_content_hash
    ON documents(org_id, content_sha256) WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS trg_documents_updated ON documents;
CREATE TRIGGER trg_documents_updated BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Chunks
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_chunks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    ordinal       INTEGER NOT NULL CHECK (ordinal >= 0),
    heading       TEXT,
    chunk_text    TEXT NOT NULL CHECK (length(chunk_text) > 0),

    start_offset  INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset    INTEGER NOT NULL,
    token_count   INTEGER NOT NULL DEFAULT 0 CHECK (token_count >= 0),
    content_sha256 TEXT NOT NULL,               -- embedding cache key

    -- Dimensions must match EMBEDDING_DIMENSIONS. Changing the embedding model
    -- to a different width requires a migration that rewrites this column.
    embedding     vector(1536),
    embedding_model TEXT,

    -- Maintained by the database so it can never drift from chunk_text.
    fts           tsvector GENERATED ALWAYS AS (
                      to_tsvector('english', coalesce(heading, '') || ' ' || chunk_text)
                  ) STORED,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chunk_span_valid CHECK (end_offset > start_offset),
    CONSTRAINT chunk_ordinal_unique UNIQUE (document_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_chunks_document_ordinal
    ON document_chunks(document_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_chunks_org ON document_chunks(org_id);
CREATE INDEX IF NOT EXISTS idx_chunks_fts ON document_chunks USING gin (fts);
CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON document_chunks(content_sha256);

-- HNSW gives better recall/latency than IVFFlat and needs no training step.
-- Cosine distance matches the normalized vectors produced by the providers.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Reusable cache so identical clause text is never embedded twice.
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_sha256 TEXT NOT NULL,
    model          TEXT NOT NULL,
    dimensions     INTEGER NOT NULL CHECK (dimensions > 0),
    embedding      vector(1536) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (content_sha256, model)
);
