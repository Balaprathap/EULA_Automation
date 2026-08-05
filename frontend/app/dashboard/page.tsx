'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { ActionItemWidget } from '@/components/ActionItemWidget';
import { ActivityFeed, type ActivityItem } from '@/components/ActivityFeed';
import { RequireAuth } from '@/components/RequireAuth';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  LegalDisclaimer,
  SectionHeading,
  SkeletonCard,
  SkeletonRows,
  StatCard,
} from '@/components/ui';
import { api } from '@/lib/api';
import { formatCost, formatDate, riskBandClasses, severityClasses } from '@/lib/format';
import type { ActionItemSummary, RiskBand, Severity } from '@/lib/types';

interface DashboardData {
  recent_documents: Array<Record<string, unknown>>;
  recent_analyses: Array<Record<string, unknown>>;
  analysis_status_counts: Record<string, number>;
  risk_distribution: Record<string, number>;
  pending_reviews: number;
  estimated_cost_usd_30d: number;
}

const SEVERITIES: Severity[] = ['critical', 'high', 'medium', 'low', 'info'];

function DashboardContent() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionSummary, setActionSummary] = useState<ActionItemSummary | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData((await api.getDashboard()) as unknown as DashboardData);
      // Non-fatal: the dashboard still renders if this widget cannot load.
      api.getActionItemSummary().then(setActionSummary).catch(() => setActionSummary(null));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the dashboard.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const activity = useMemo<ActivityItem[]>(() => {
    if (!data) return [];
    const documents = data.recent_documents.map((doc) => ({
      id: String(doc.id),
      kind: 'document' as const,
      title: String(doc.title ?? 'Untitled'),
      href: `/documents/${String(doc.id)}`,
      at: String(doc.created_at),
    }));
    const analyses = data.recent_analyses.map((run) => ({
      id: String(run.id),
      kind: 'analysis' as const,
      title: String(run.document_title ?? 'Untitled'),
      href: `/documents/${String(run.document_id)}/analyses/${String(run.id)}`,
      at: String(run.created_at),
      status: String(run.status),
      score: run.overall_score as number | null,
    }));
    return [...documents, ...analyses]
      .sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime())
      .slice(0, 8);
  }, [data]);

  if (loading) {
    return (
      <div className="space-y-8">
        <SectionHeading title="Dashboard" description="Automated EULA Compliance Extraction" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
        <SkeletonRows rows={4} />
      </div>
    );
  }

  if (error) return <ErrorState error={error} onRetry={load} />;
  if (!data) return null;

  const statusCounts = data.analysis_status_counts;
  const completedAnalyses = (statusCounts.complete ?? 0) + (statusCounts.partial ?? 0);
  const highRisk =
    (data.risk_distribution.critical ?? 0) + (data.risk_distribution.high ?? 0);
  const hasContent = data.recent_documents.length > 0;

  return (
    <div className="space-y-8">
      <SectionHeading
        title="Dashboard"
        description="Automated EULA Compliance Extraction"
        action={
          <>
            <Link href="/documents/upload">
              <Button>Upload an agreement</Button>
            </Link>
            <Link href="/policies">
              <Button variant="secondary">Manage policies</Button>
            </Link>
          </>
        }
      />

      {!hasContent ? (
        <EmptyState
          title="No agreements yet"
          description="Upload a PDF, DOCX, or TXT agreement — or paste the text directly — to run your first compliance analysis."
          action={
            <Link href="/documents/upload">
              <Button>Upload your first agreement</Button>
            </Link>
          }
        />
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Recent documents"
          value={data.recent_documents.length}
          hint="Most recently added"
          help="The five most recently added agreements. Open Documents for the full library."
        />
        <StatCard
          label="Completed analyses"
          value={completedAnalyses}
          hint={
            statusCounts.partial
              ? `${statusCounts.partial} finished with categories needing review`
              : undefined
          }
          help="Analyses that finished, including partial runs where some categories need human review."
        />
        <StatCard
          label="Awaiting review"
          value={data.pending_reviews}
          tone={data.pending_reviews > 0 ? 'warning' : 'neutral'}
          help="Verified findings nobody has accepted, dismissed or escalated yet."
        />
        <StatCard
          label="High-risk findings"
          value={highRisk}
          tone={highRisk > 0 ? 'danger' : 'success'}
          hint="Critical + high severity"
          help="Verified findings scored critical or high by your policy's severity weights."
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <StatCard
          label="AI cost, last 30 days"
          value={formatCost(data.estimated_cost_usd_30d)}
          help="Estimated from provider-reported token counts and the rates in your configuration. Verify against your provider invoice."
        />
        <StatCard
          label="Analyses in flight"
          value={(statusCounts.queued ?? 0) + (statusCounts.running ?? 0)}
          hint={statusCounts.failed ? `${statusCounts.failed} failed` : undefined}
          help="Queued or currently running. Analysis happens in a background worker."
        />
      </div>

      {Object.keys(data.risk_distribution).length > 0 ? (
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Verified findings by severity
          </h2>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Only findings whose quote was located in the source document are counted.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {SEVERITIES.map((severity) => (
              <span
                key={severity}
                className={`rounded-full border px-3 py-1 text-xs font-medium ${severityClasses(severity)}`}
              >
                {severity}: {data.risk_distribution[severity] ?? 0}
              </span>
            ))}
          </div>
        </Card>
      ) : null}

      <ActionItemWidget summary={actionSummary} />

      <div className="grid gap-6 lg:grid-cols-2">
        <ActivityFeed items={activity} />

        <Card>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Recent analyses
          </h2>
          {data.recent_analyses.length === 0 ? (
            <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">
              No analyses have been run yet.
            </p>
          ) : (
            <ul className="mt-3 divide-y divide-slate-100 dark:divide-slate-800">
              {data.recent_analyses.map((analysis) => (
                <li key={String(analysis.id)} className="py-2">
                  <Link
                    href={`/documents/${String(analysis.document_id)}/analyses/${String(analysis.id)}`}
                    className="flex items-center justify-between gap-3 rounded px-1 py-1 hover:bg-slate-50 dark:hover:bg-slate-800"
                  >
                    <span className="min-w-0 flex-1 truncate text-sm">
                      {String(analysis.document_title ?? 'Untitled')}
                    </span>
                    <Badge
                      tone={
                        analysis.status === 'complete'
                          ? 'success'
                          : analysis.status === 'failed'
                            ? 'danger'
                            : analysis.status === 'partial'
                              ? 'warning'
                              : 'neutral'
                      }
                    >
                      {String(analysis.status)}
                    </Badge>
                    {analysis.overall_score !== null && analysis.overall_score !== undefined ? (
                      <span
                        className={`text-sm font-semibold tabular-nums ${riskBandClasses(
                          analysis.risk_band as RiskBand,
                        )}`}
                      >
                        {String(analysis.overall_score)}
                      </span>
                    ) : null}
                    <time
                      dateTime={String(analysis.created_at)}
                      className="hidden text-xs text-slate-500 sm:block dark:text-slate-400"
                    >
                      {formatDate(String(analysis.created_at))}
                    </time>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <LegalDisclaimer />
    </div>
  );
}

export default function DashboardPage() {
  return (
    <RequireAuth>
      <DashboardContent />
    </RequireAuth>
  );
}
