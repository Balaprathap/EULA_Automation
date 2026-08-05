'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { AnalysisProgress } from '@/components/AnalysisProgress';
import { EvidencePane } from '@/components/EvidencePane';
import { FindingCard } from '@/components/FindingCard';
import {
  DEFAULT_FILTERS,
  type FindingFilters,
  FindingsToolbar,
  type SortKey,
  applyFilters,
} from '@/components/FindingsToolbar';
import { ReportPanel } from '@/components/ReportPanel';
import { RequireAuth } from '@/components/RequireAuth';
import { useToast } from '@/components/Toast';
import {
  Badge,
  Breadcrumbs,
  Button,
  Card,
  EmptyState,
  ErrorState,
  InfoTip,
  LegalDisclaimer,
  SectionHeading,
  SkeletonRows,
  StatCard,
  Warning,
} from '@/components/ui';
import { api } from '@/lib/api';
import {
  downloadBlob,
  formatCost,
  humanizeCategory,
  riskBandClasses,
  slugify,
  toCsv,
} from '@/lib/format';
import { PREFERENCE_KEYS, readPreference, writePreference } from '@/lib/preferences';
import type { Analysis, Finding } from '@/lib/types';

const POLL_MS = 2500;
const TERMINAL = ['complete', 'partial', 'failed', 'cancelled'];

const CSV_COLUMNS = [
  'category',
  'effective_severity',
  'machine_severity',
  'override_severity',
  'model_confidence',
  'severity_weight',
  'weighted_risk',
  'verification_status',
  'review_status',
  'plain_summary',
  'why_it_matters',
  'quote',
];

function FindingsWorkspace() {
  const { id, analysisId } = useParams<{ id: string; analysisId: string }>();
  const toast = useToast();

  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [documentTitle, setDocumentTitle] = useState('this agreement');
  const [filters, setFilters] = useState<FindingFilters>(DEFAULT_FILTERS);
  const [sort, setSort] = useState<SortKey>('severity');

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setFilters(readPreference<FindingFilters>(PREFERENCE_KEYS.findingFilters, DEFAULT_FILTERS));
    setSort(readPreference<SortKey>(PREFERENCE_KEYS.findingSort, 'severity'));
  }, []);

  const updateFilters = useCallback((next: FindingFilters) => {
    setFilters(next);
    writePreference(PREFERENCE_KEYS.findingFilters, next);
  }, []);

  const updateSort = useCallback((next: SortKey) => {
    setSort(next);
    writePreference(PREFERENCE_KEYS.findingSort, next);
  }, []);

  const loadFindings = useCallback(async () => {
    const rows = await api.listFindings(analysisId, { include_quarantined: true });
    setFindings(rows);
    setSelectedId(
      (current) => current ?? rows.find((r) => r.verification_status === 'verified')?.id ?? null,
    );
  }, [analysisId]);

  const poll = useCallback(async () => {
    try {
      const current = await api.getAnalysis(analysisId);
      setAnalysis(current);
      setError(null);
      if (TERMINAL.includes(current.status)) {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
        await loadFindings();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the analysis.');
    } finally {
      setLoading(false);
    }
  }, [analysisId, loadFindings]);

  useEffect(() => {
    api
      .getDocument(id)
      .then((doc) => setDocumentTitle(doc.title))
      .catch(() => setDocumentTitle('this agreement'));
  }, [id]);

  useEffect(() => {
    void poll();
    pollRef.current = setInterval(() => void poll(), POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [poll]);

  const categories = useMemo(
    () => Array.from(new Set(findings.map((f) => f.category))).sort(),
    [findings],
  );

  const visible = useMemo(() => applyFilters(findings, filters, sort), [findings, filters, sort]);

  const severityCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const finding of findings) {
      if (finding.verification_status !== 'verified') continue;
      counts[finding.effective_severity] = (counts[finding.effective_severity] ?? 0) + 1;
    }
    return counts;
  }, [findings]);

  const selected = visible.find((f) => f.id === selectedId) ?? visible[0] ?? null;

  // Keyboard navigation: j/k or arrows move between findings.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (visible.length === 0) return;

      const index = visible.findIndex((f) => f.id === selected?.id);
      if (event.key === 'j' || event.key === 'ArrowDown') {
        event.preventDefault();
        setSelectedId(visible[Math.min(visible.length - 1, index + 1)].id);
      } else if (event.key === 'k' || event.key === 'ArrowUp') {
        event.preventDefault();
        setSelectedId(visible[Math.max(0, index - 1)].id);
      }
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [visible, selected]);

  async function retry() {
    try {
      const created = await api.startAnalysis(id);
      toast.success('New analysis queued.');
      window.location.href = `/documents/${id}/analyses/${created.id}`;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not restart the analysis.');
    }
  }

  function exportFindings(format: 'json' | 'csv') {
    const stem = `clauseguard-findings-${slugify(analysisId)}`;
    if (format === 'json') {
      downloadBlob(JSON.stringify(visible, null, 2), `${stem}.json`, 'application/json');
    } else {
      const rows = visible.map((f) => ({ ...f }) as unknown as Record<string, unknown>);
      downloadBlob(toCsv(rows, CSV_COLUMNS), `${stem}.csv`, 'text/csv');
    }
    toast.success(`Exported ${visible.length} findings as ${format.toUpperCase()}.`);
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <SectionHeading title="Compliance findings" />
        <SkeletonRows rows={6} />
      </div>
    );
  }
  if (error && !analysis) return <ErrorState error={error} onRetry={poll} />;
  if (!analysis) return null;

  const running = !TERMINAL.includes(analysis.status);

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: 'Dashboard', href: '/dashboard' },
          { label: 'Documents', href: '/documents' },
          { label: 'Document', href: `/documents/${id}` },
          { label: 'Analysis' },
        ]}
      />

      <SectionHeading
        title="Compliance findings"
        description={
          running
            ? 'Analysis in progress — results appear as each stage completes.'
            : 'Every finding below links to the exact clause it came from.'
        }
        action={
          !running ? (
            <>
              <Button variant="secondary" onClick={() => exportFindings('csv')}>
                Export CSV
              </Button>
              <Button variant="secondary" onClick={() => exportFindings('json')}>
                Export JSON
              </Button>
              <Button variant="secondary" onClick={() => window.print()}>
                Print report
              </Button>
            </>
          ) : undefined
        }
      />

      {running ? (
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Analysis in progress
          </h2>
          <div className="mt-4">
            <AnalysisProgress analysis={analysis} onRetry={retry} />
          </div>
        </Card>
      ) : null}

      {analysis.status === 'failed' ? (
        <div className="space-y-3">
          <ErrorState
            error={
              analysis.error_message ??
              'The analysis failed. No partial results were recorded for this run.'
            }
          />
          <Button onClick={retry}>Retry analysis</Button>
        </div>
      ) : null}

      {analysis.status === 'partial' ? (
        <Warning title="Partial analysis">
          Some categories could not be completed and are marked as needing review. The findings
          below are real and verified, but this is not a complete pass over the agreement. Expand
          the per-category detail to see which categories are affected.
        </Warning>
      ) : null}

      {analysis.degraded_retrieval ? (
        <Warning title="Degraded retrieval">
          {analysis.degraded_reason ??
            'A retrieval fallback was used for at least one category, so some clauses may have been missed.'}{' '}
          Confidence was capped for anything derived from degraded retrieval.
        </Warning>
      ) : null}

      {!running ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <StatCard
            label="Overall score"
            value={
              <span className={riskBandClasses(analysis.risk_band)}>
                {analysis.overall_score ?? '—'}
              </span>
            }
            hint={`${analysis.risk_band ?? 'not scored'} risk`}
            help="0–100, computed from verified findings using your policy's severity weights. Many low-severity findings cannot outrank one critical clause."
          />
          <StatCard
            label="Verified findings"
            value={analysis.finding_count}
            help="Findings whose quoted text was located in the source document."
          />
          <StatCard
            label="Needs review"
            value={analysis.review_count}
            tone={analysis.review_count > 0 ? 'warning' : 'neutral'}
            help="Categories the system could not decide confidently, handed to a human."
          />
          <StatCard
            label="Quarantined"
            value={analysis.quarantine_count}
            tone={analysis.quarantine_count > 0 ? 'warning' : 'success'}
            hint={`${analysis.verification_pass_rate ?? 100}% verification pass rate`}
            help="Proposed findings whose quote could not be found in the document. They are excluded from the score and never shown as confirmed."
          />
          <StatCard
            label="AI cost"
            value={formatCost(analysis.estimated_cost_usd)}
            hint={`${(analysis.input_tokens + analysis.output_tokens).toLocaleString()} tokens`}
            help="Estimated from provider-reported token counts and your configured rates."
          />
        </div>
      ) : null}

      <ReportPanel
        analysisId={analysisId}
        analysisStatus={analysis.status}
        documentTitle={documentTitle}
      />

      {analysis.executive_summary ? (
        <Card>
          <h2 className="flex items-center text-sm font-semibold text-slate-900 dark:text-slate-100">
            Executive summary
            <InfoTip label="Executive summary">
              Written only from findings that were already verified and scored. The summary model
              cannot introduce new findings or change any severity.
            </InfoTip>
          </h2>
          <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700 dark:text-slate-300">
            {analysis.executive_summary}
          </p>
        </Card>
      ) : null}

      {!running ? (
        <>
          <FindingsToolbar
            filters={filters}
            onFiltersChange={updateFilters}
            sort={sort}
            onSortChange={updateSort}
            categories={categories}
            severityCounts={severityCounts}
            quarantineCount={analysis.quarantine_count}
            visibleCount={visible.length}
            totalCount={findings.length}
            onReset={() => updateFilters({ ...DEFAULT_FILTERS, showQuarantined: filters.showQuarantined })}
          />

          {visible.length === 0 ? (
            <EmptyState
              title={findings.length === 0 ? 'No findings' : 'No findings match these filters'}
              description={
                findings.length === 0
                  ? 'No clauses matching this policy were found in the agreement. That is a real result, not an error — the categories reviewed are listed in the analysis detail above.'
                  : 'Clear or widen the filters to see more.'
              }
              action={
                findings.length > 0 ? (
                  <Button variant="secondary" onClick={() => updateFilters(DEFAULT_FILTERS)}>
                    Clear filters
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <div className="grid gap-6 lg:grid-cols-2">
              <div className="space-y-3">
                <p className="no-print text-xs text-slate-500 dark:text-slate-400">
                  Tip: press <kbd className="rounded border border-slate-300 px-1 dark:border-slate-700">j</kbd>{' '}
                  and <kbd className="rounded border border-slate-300 px-1 dark:border-slate-700">k</kbd>{' '}
                  to move between findings.
                </p>
                {visible.map((finding) => (
                  <FindingCard
                    key={finding.id}
                    finding={finding}
                    selected={selected?.id === finding.id}
                    onSelect={() => setSelectedId(finding.id)}
                    onReviewed={(updated) =>
                      setFindings((current) =>
                        current.map((f) => (f.id === finding.id ? { ...f, ...updated } : f)),
                      )
                    }
                  />
                ))}
              </div>

              <div className="lg:sticky lg:top-20 lg:self-start">
                <Card>
                  <h2 className="flex items-center text-sm font-semibold text-slate-900 dark:text-slate-100">
                    Source document
                    <InfoTip label="Source document">
                      The highlight uses offsets recomputed during verification, so it always sits
                      on the exact text the finding came from.
                    </InfoTip>
                  </h2>
                  {selected ? (
                    <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                      {humanizeCategory(selected.category)}
                      {selected.chunk_heading ? ` · ${selected.chunk_heading}` : ''}
                    </p>
                  ) : null}
                  <div className="mt-4">
                    {selected ? (
                      <EvidencePane finding={selected} />
                    ) : (
                      <p className="text-sm text-slate-500 dark:text-slate-400">
                        Select a finding to see its source text.
                      </p>
                    )}
                  </div>
                </Card>
              </div>
            </div>
          )}
        </>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <Badge tone="neutral">Analysis {analysis.id.slice(0, 8)}</Badge>
        <Link
          href={`/documents/${id}`}
          className="text-sm text-slate-500 underline hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
        >
          Back to the document
        </Link>
      </div>

      <LegalDisclaimer />
    </div>
  );
}

export default function AnalysisPage() {
  return (
    <RequireAuth>
      <FindingsWorkspace />
    </RequireAuth>
  );
}
