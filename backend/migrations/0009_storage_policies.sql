-- 0009: Private Supabase Storage bucket and its access policies.
--
-- Object keys are org-prefixed: {org_id}/{document_id}/{filename}
-- The bucket is private; documents are never given a public URL. Downloads use
-- short-lived signed URLs minted by the API after an ownership check.

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'documents',
    'documents',
    FALSE,
    10485760,  -- 10 MB, matches MAX_UPLOAD_MB
    ARRAY[
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'text/plain'
    ]
)
ON CONFLICT (id) DO UPDATE
    SET public = FALSE,
        file_size_limit = EXCLUDED.file_size_limit,
        allowed_mime_types = EXCLUDED.allowed_mime_types;

-- The first path segment must be the caller's organization id.
DROP POLICY IF EXISTS documents_storage_select ON storage.objects;
CREATE POLICY documents_storage_select ON storage.objects
    FOR SELECT USING (
        bucket_id = 'documents'
        AND (storage.foldername(name))[1] = auth_org_id()::text
    );

DROP POLICY IF EXISTS documents_storage_insert ON storage.objects;
CREATE POLICY documents_storage_insert ON storage.objects
    FOR INSERT WITH CHECK (
        bucket_id = 'documents'
        AND (storage.foldername(name))[1] = auth_org_id()::text
    );

DROP POLICY IF EXISTS documents_storage_delete ON storage.objects;
CREATE POLICY documents_storage_delete ON storage.objects
    FOR DELETE USING (
        bucket_id = 'documents'
        AND (storage.foldername(name))[1] = auth_org_id()::text
    );
