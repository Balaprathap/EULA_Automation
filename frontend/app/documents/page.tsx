'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { RequireAuth } from '@/components/RequireAuth';
import { useToast } from '@/components/Toast';
import {
  Badge,
  Breadcrumbs,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  SearchInput,
  SectionHeading,
  SkeletonRows,
} from '@/components/ui';
import { api } from '@/lib/api';
import { formatBytes, formatDate } from '@/lib/format';
import { PREFERENCE_KEYS, readPreference, writePreference } from '@/lib/preferences';
import type { Document } from '@/lib/types';

function statusTone(status: Document['status']) {
  if (status === 'ready') return 'success' as const;
  if (status === 'failed') return 'danger' as const;
  return 'neutral' as const;
}

function DocumentLibrary() {
  const toast = useToast();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Document | null>(null);

  // Restore the last search once on mount.
  useEffect(() => {
    setSearch(readPreference<string>(PREFERENCE_KEYS.documentSearch, ''));
  }, []);

  const load = useCallback(async (term: string) => {
    setLoading(true);
    setError(null);
    try {
      const page = await api.listDocuments({ limit: 50, search: term || undefined });
      setDocuments(page.items);
      setTotal(page.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load documents.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const handle = setTimeout(() => {
      writePreference(PREFERENCE_KEYS.documentSearch, search);
      void load(search);
    }, 250);
    return () => clearTimeout(handle);
  }, [search, load]);

  async function confirmDelete() {
    const target = pendingDelete;
    if (!target) return;
    setPendingDelete(null);
    try {
      await api.deleteDocument(target.id);
      setDocuments((current) => current.filter((d) => d.id !== target.id));
      setTotal((current) => Math.max(0, current - 1));
      toast.success(`Deleted "${target.title}".`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not delete the document.');
    }
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Dashboard', href: '/dashboard' }, { label: 'Documents' }]} />

      <SectionHeading
        title="Documents"
        description={total > 0 ? `${total} agreement${total === 1 ? '' : 's'} in your library` : undefined}
        action={
          <Link href="/documents/upload">
            <Button>Add an agreement</Button>
          </Link>
        }
      />

      <SearchInput
        label="Search documents"
        placeholder="Search by title or vendor"
        value={search}
        onChange={setSearch}
        className="max-w-sm"
      />

      {error ? <ErrorState error={error} onRetry={() => void load(search)} /> : null}
      {loading ? <SkeletonRows rows={5} /> : null}

      {!loading && documents.length === 0 && !error ? (
        <EmptyState
          title={search ? 'No documents match that search' : 'No documents yet'}
          description={
            search
              ? 'Try a different title or vendor name, or clear the search.'
              : 'Upload a PDF, DOCX, or TXT agreement, or paste the text directly.'
          }
          action={
            search ? (
              <Button variant="secondary" onClick={() => setSearch('')}>
                Clear search
              </Button>
            ) : (
              <Link href="/documents/upload">
                <Button>Add an agreement</Button>
              </Link>
            )
          }
        />
      ) : null}

      {!loading && documents.length > 0 ? (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-sm">
            <caption className="sr-only">Your uploaded agreements</caption>
            <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-400">
              <tr>
                <th scope="col" className="px-4 py-3">Title</th>
                <th scope="col" className="hidden px-4 py-3 md:table-cell">Vendor</th>
                <th scope="col" className="hidden px-4 py-3 sm:table-cell">Type</th>
                <th scope="col" className="hidden px-4 py-3 lg:table-cell">Pages</th>
                <th scope="col" className="hidden px-4 py-3 lg:table-cell">Size</th>
                <th scope="col" className="px-4 py-3">Status</th>
                <th scope="col" className="hidden px-4 py-3 sm:table-cell">Added</th>
                <th scope="col" className="px-4 py-3"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {documents.map((document) => (
                <tr key={document.id} className="transition hover:bg-slate-50 dark:hover:bg-slate-800/50">
                  <td className="px-4 py-3">
                    <Link
                      href={`/documents/${document.id}`}
                      className="rounded font-medium text-slate-900 hover:underline dark:text-slate-100"
                    >
                      {document.title}
                    </Link>
                  </td>
                  <td className="hidden px-4 py-3 text-slate-600 md:table-cell dark:text-slate-400">
                    {document.vendor_name ?? '—'}
                  </td>
                  <td className="hidden px-4 py-3 uppercase text-slate-500 sm:table-cell dark:text-slate-400">
                    {document.source_type}
                  </td>
                  <td className="hidden px-4 py-3 tabular-nums text-slate-600 lg:table-cell dark:text-slate-400">
                    {document.page_count ?? '—'}
                  </td>
                  <td className="hidden px-4 py-3 tabular-nums text-slate-600 lg:table-cell dark:text-slate-400">
                    {formatBytes(document.file_size_bytes)}
                  </td>
                  <td className="px-4 py-3">
                    <Badge tone={statusTone(document.status)}>{document.status}</Badge>
                  </td>
                  <td className="hidden px-4 py-3 text-slate-500 sm:table-cell dark:text-slate-400">
                    {formatDate(document.created_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => setPendingDelete(document)}
                      aria-label={`Delete ${document.title}`}
                      className="rounded px-1 text-xs text-red-600 hover:underline dark:text-red-400"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : null}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this document?"
        destructive
        confirmLabel="Delete"
        body={
          <>
            <p>
              <strong>{pendingDelete?.title}</strong> will be removed, along with its analyses and
              findings.
            </p>
            <p className="mt-2">This cannot be undone.</p>
          </>
        }
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}

export default function DocumentsPage() {
  return (
    <RequireAuth>
      <DocumentLibrary />
    </RequireAuth>
  );
}
