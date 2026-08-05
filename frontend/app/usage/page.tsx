'use client';

import { useCallback, useEffect, useState } from 'react';

import { RequireAuth } from '@/components/RequireAuth';
import {
  Breadcrumbs,
  Card,
  EmptyState,
  ErrorState,
  InfoTip,
  SectionHeading,
  SkeletonCard,
  StatCard,
  controlClasses,
} from '@/components/ui';
import { api } from '@/lib/api';
import { formatCost } from '@/lib/format';

function UsageView() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (window: number) => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getUsage(window));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load usage.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(days);
  }, [days, load]);

  if (loading) {
    return (
      <div className="space-y-6">
        <SectionHeading title="Usage and cost" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      </div>
    );
  }
  if (error) return <ErrorState error={error} onRetry={() => void load(days)} />;
  if (!data) return null;

  const maxCost = Math.max(...(data.daily ?? []).map((d: any) => d.estimated_cost_usd), 0.0001);

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Dashboard', href: '/dashboard' }, { label: 'Usage' }]} />

      <SectionHeading
        title="Usage and cost"
        description="Token consumption and estimated spend for your organization."
        action={
          <>
            <label className="sr-only" htmlFor="usage-period">
              Reporting period
            </label>
            <select
              id="usage-period"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className={controlClasses}
            >
              {[7, 30, 90, 365].map((option) => (
                <option key={option} value={option}>
                  Last {option} days
                </option>
              ))}
            </select>
          </>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Analyses"
          value={data.analyses_run}
          help="Analysis runs started in this period, including partial and failed runs."
        />
        <StatCard
          label="Documents"
          value={data.documents_uploaded}
          help="Agreements added in this period."
        />
        <StatCard
          label="Total tokens"
          value={data.total_tokens.toLocaleString()}
          hint={`${data.cached_input_tokens.toLocaleString()} served from cache`}
          help="Tokens are how AI providers measure text. Cached tokens are re-used prompt content, billed at a lower rate — a higher cache share means lower cost."
        />
        <StatCard
          label="Estimated cost"
          value={formatCost(data.estimated_cost_usd)}
          help="Calculated from provider-reported token counts and the rates in your configuration. Treat as an estimate and verify against your provider invoice."
        />
      </div>

      <Card>
        <h2 className="flex items-center text-sm font-semibold text-slate-900 dark:text-slate-100">
          By event type
          <InfoTip label="Event types">
            <strong>llm_extraction</strong> is clause extraction, one call per policy category.{' '}
            <strong>embedding</strong> is turning clause text into vectors for search.{' '}
            <strong>llm_summary</strong> is the executive summary.
          </InfoTip>
        </h2>
        {(data.by_event_type ?? []).length === 0 ? (
          <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">No usage recorded in this period.</p>
        ) : (
          <table className="mt-3 w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
              <tr>
                <th className="py-2">Event</th>
                <th className="py-2">Count</th>
                <th className="py-2">Input</th>
                <th className="py-2">Output</th>
                <th className="py-2">Cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.by_event_type.map((row: any) => (
                <tr key={row.event_type}>
                  <td className="py-2">{row.event_type}</td>
                  <td className="py-2">{row.events}</td>
                  <td className="py-2">{row.input_tokens.toLocaleString()}</td>
                  <td className="py-2">{row.output_tokens.toLocaleString()}</td>
                  <td className="py-2">{formatCost(row.estimated_cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Daily spend</h2>
        {(data.daily ?? []).length === 0 ? (
          <EmptyState title="No activity" description="Run an analysis to see usage here." />
        ) : (
          <div className="mt-4 flex h-40 items-end gap-1">
            {data.daily.map((day: any) => (
              <div
                key={day.day}
                title={`${day.day}: ${formatCost(day.estimated_cost_usd)}`}
                className="flex-1 rounded-t bg-slate-800 dark:bg-slate-300"
                style={{
                  height: `${Math.max(3, (day.estimated_cost_usd / maxCost) * 100)}%`,
                }}
              />
            ))}
          </div>
        )}
      </Card>

      <p className="text-xs text-slate-500 dark:text-slate-400">
        Costs are estimates calculated from the token counts reported by the provider and the rates
        configured in your environment. Verify against your provider invoice before relying on them.
      </p>
    </div>
  );
}

export default function UsagePage() {
  return (
    <RequireAuth>
      <UsageView />
    </RequireAuth>
  );
}
