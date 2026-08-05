'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { RequireAuth } from '@/components/RequireAuth';
import { Button, Card, ErrorState, Field, Spinner, inputClasses } from '@/components/ui';
import { api } from '@/lib/api';
import { formatBytes, formatDate, riskBandClasses } from '@/lib/format';
import type { Analysis, Document, Policy, RiskBand } from '@/lib/types';

function DocumentDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const [document, setDocument] = useState<Document | null>(null);
  const [analyses, setAnalyses] = useState<Analysis[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [policyId, setPolicyId] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [doc, analysisPage, policyList] = await Promise.all([
        api.getDocument(id),
        api.listAnalyses(),
        api.listPolicies(),
      ]);
      setDocument(doc);
      setAnalyses(analysisPage.items.filter((a) => a.document_id === id));
      setPolicies(policyList);
      setPolicyId(policyList.find((p) => p.is_default)?.id ?? policyList[0]?.id ?? '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the document.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function startAnalysis() {
    setStarting(true);
    setError(null);
    try {
      const analysis = await api.startAnalysis(id, policyId || undefined);
      router.push(`/documents/${id}/analyses/${analysis.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the analysis.');
      setStarting(false);
    }
  }

  if (loading) return <Spinner label="Loading document" />;
  if (error && !document) return <ErrorState error={error} onRetry={load} />;
  if (!document) return null;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/documents" className="text-sm text-slate-500 hover:underline dark:text-slate-400">
          &larr; All documents
        </Link>
        <h1 className="mt-2 text-2xl font-bold text-slate-900 dark:text-slate-100">{document.title}</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          {document.vendor_name ? `${document.vendor_name} - ` : ''}
          {document.source_type.toUpperCase()}
          {document.page_count ? ` - ${document.page_count} pages` : ''}
          {document.char_count ? ` - ${document.char_count.toLocaleString()} characters` : ''}
          {document.file_size_bytes ? ` - ${formatBytes(document.file_size_bytes)}` : ''}
        </p>
      </div>

      {document.status === 'failed' ? (
        <ErrorState error={document.error_message ?? 'This document could not be processed.'} />
      ) : null}

      <Card className="space-y-4">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Run a compliance analysis</h2>
        <Field label="Policy">
          <select
            value={policyId}
            onChange={(e) => setPolicyId(e.target.value)}
            className={inputClasses}
          >
            {policies.map((policy) => (
              <option key={policy.id} value={policy.id}>
                {policy.name} (v{policy.version}) - {policy.rule_count} categories
              </option>
            ))}
          </select>
        </Field>
        {error ? <ErrorState error={error} /> : null}
        <Button onClick={startAnalysis} disabled={starting || document.status !== 'ready'}>
          {starting ? 'Starting...' : 'Start analysis'}
        </Button>
        {document.status !== 'ready' ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            This document is {document.status}. Analysis becomes available once it is ready.
          </p>
        ) : null}
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Analysis history</h2>
        {analyses.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">
            No analyses have been run against this document yet.
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100">
            {analyses.map((analysis) => (
              <li key={analysis.id} className="py-3">
                <Link
                  href={`/documents/${id}/analyses/${analysis.id}`}
                  className="flex flex-wrap items-center justify-between gap-3 hover:underline"
                >
                  <span className="text-sm">{formatDate(analysis.created_at)}</span>
                  <span className="text-xs text-slate-500 dark:text-slate-400">{analysis.status}</span>
                  <span className="text-xs text-slate-500 dark:text-slate-400">
                    {analysis.finding_count} findings
                    {analysis.quarantine_count > 0
                      ? `, ${analysis.quarantine_count} quarantined`
                      : ''}
                  </span>
                  {analysis.overall_score !== null ? (
                    <span
                      className={`text-sm font-semibold ${riskBandClasses(
                        analysis.risk_band as RiskBand,
                      )}`}
                    >
                      {analysis.overall_score} / 100
                    </span>
                  ) : null}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

export default function DocumentPage() {
  return (
    <RequireAuth>
      <DocumentDetail />
    </RequireAuth>
  );
}
