'use client';

import { useEffect, useRef, useState } from 'react';

import { api } from '@/lib/api';
import { splitForHighlight } from '@/lib/format';
import type { Evidence, Finding } from '@/lib/types';

import { ErrorState, Spinner } from './ui';

/**
 * Source document pane.
 *
 * Highlighting uses the absolute offsets recomputed during evidence
 * verification, so the highlight is guaranteed to sit on the exact text the
 * finding was derived from. If the offsets do not resolve, the pane says so
 * rather than highlighting something approximate.
 */
export function EvidencePane({ finding }: { finding: Finding }) {
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const markRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setEvidence(null);

    api
      .getEvidence(finding.id)
      .then((result) => {
        if (!cancelled) setEvidence(result);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Evidence could not be loaded.');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [finding.id]);

  // Scroll the verified span into view once it renders.
  useEffect(() => {
    if (markRef.current) {
      markRef.current.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [evidence]);

  if (loading) return <Spinner label="Loading source text" />;

  if (finding.verification_status === 'quarantined') {
    return (
      <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-200">
        <p className="font-semibold">No verified evidence</p>
        <p className="mt-1">
          {finding.quarantine_reason ??
            'The quoted text could not be located in the source document.'}
        </p>
        <p className="mt-2 text-xs">
          This finding is excluded from the risk score and is shown only for transparency.
        </p>
      </div>
    );
  }

  if (error) return <ErrorState error={error} />;
  if (!evidence) return null;

  const body = evidence.surrounding_text ?? evidence.chunk_text ?? evidence.quote;
  const base = evidence.surrounding_text ? (evidence.surrounding_start_offset ?? 0) : null;

  const parts =
    base !== null
      ? splitForHighlight(body, evidence.doc_start_offset, evidence.doc_end_offset, base)
      : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
        {evidence.chunk_heading ? (
          <span className="font-medium text-slate-700 dark:text-slate-300">{evidence.chunk_heading}</span>
        ) : null}
        <span>
          characters {evidence.doc_start_offset}-{evidence.doc_end_offset}
        </span>
        <span className="rounded-full bg-emerald-100 px-2 py-0.5 font-medium text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
          verified ({evidence.verification_method})
        </span>
      </div>

      <div className="print-block max-h-[28rem] overflow-y-auto rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
        {parts ? (
          <p className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-slate-700 dark:text-slate-300">
            {parts.before}
            <mark className="evidence" ref={markRef as never}>
              {parts.highlight}
            </mark>
            {parts.after}
          </p>
        ) : (
          <>
            <p className="mb-2 text-xs text-amber-700 dark:text-amber-300">
              The surrounding context could not be aligned; showing the verified quote on its own.
            </p>
            <p className="whitespace-pre-wrap font-mono text-xs leading-relaxed">
              <mark className="evidence">{evidence.quote}</mark>
            </p>
          </>
        )}
      </div>
    </div>
  );
}
