'use client';

import { useCallback, useEffect, useState } from 'react';

import { RequireAuth } from '@/components/RequireAuth';
import { Card, ErrorState, Spinner } from '@/components/ui';
import { ApiClientError, api } from '@/lib/api';
import { formatCost } from '@/lib/format';

function AdminMetrics() {
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getAdminMetrics());
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 403) {
        setForbidden(true);
      } else {
        setError(err instanceof Error ? err.message : 'Could not load metrics.');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const handle = setInterval(() => void load(), 15000);
    return () => clearInterval(handle);
  }, [load]);

  if (loading && !data) return <Spinner label="Loading metrics" />;

  if (forbidden) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white p-10 text-center dark:border-slate-800 dark:bg-slate-900">
        <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Administrators only</h1>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
          This page requires an administrator or owner role. The API refused the request, which is
          the authoritative check.
        </p>
      </div>
    );
  }

  if (error) return <ErrorState error={error} onRetry={load} />;
  if (!data) return null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">System metrics</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Refreshes every 15 seconds.</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ['Analyses', data.analyses_total],
          ['Succeeded', data.analyses_succeeded],
          ['Partial', data.analyses_partial],
          ['Failed', data.analyses_failed],
        ].map(([label, value]) => (
          <Card key={label as string}>
            <p className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</p>
            <p className="mt-1 text-3xl font-bold">{value as number}</p>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <p className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Success rate</p>
          <p className="mt-1 text-2xl font-bold">{data.success_rate}%</p>
        </Card>
        <Card>
          <p className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Error rate</p>
          <p className="mt-1 text-2xl font-bold">{data.error_rate}%</p>
        </Card>
        <Card>
          <p className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Verification pass rate</p>
          <p className="mt-1 text-2xl font-bold">
            {data.verification_pass_rate ?? 'not measured'}
            {data.verification_pass_rate !== null ? '%' : ''}
          </p>
        </Card>
        <Card>
          <p className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">p95 analysis time</p>
          <p className="mt-1 text-2xl font-bold">
            {data.p95_analysis_seconds !== null ? `${data.p95_analysis_seconds}s` : 'not measured'}
          </p>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Average stage latency</h2>
          {Object.keys(data.average_stage_latency_ms ?? {}).length === 0 ? (
            <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">No completed analyses yet.</p>
          ) : (
            <ul className="mt-3 space-y-2 text-sm">
              {Object.entries(data.average_stage_latency_ms as Record<string, number>).map(
                ([stage, ms]) => (
                  <li key={stage} className="flex justify-between">
                    <span className="text-slate-600 dark:text-slate-400">{stage}</span>
                    <span className="font-mono">{ms.toFixed(0)} ms</span>
                  </li>
                ),
              )}
            </ul>
          )}
        </Card>

        <Card>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Infrastructure</h2>
          <ul className="mt-3 space-y-2 text-sm">
            {[
              ['Database', data.database_connected],
              ['Redis', data.redis_connected],
            ].map(([label, ok]) => (
              <li key={label as string} className="flex items-center justify-between">
                <span className="text-slate-600 dark:text-slate-400">{label}</span>
                <span className={ok ? 'text-emerald-700' : 'text-red-700'}>
                  {ok ? 'connected' : 'unavailable'}
                </span>
              </li>
            ))}
            <li className="flex items-center justify-between">
              <span className="text-slate-600 dark:text-slate-400">Queue depth</span>
              <span className="font-mono">
                {data.queue_depth >= 0 ? data.queue_depth : 'unknown'}
              </span>
            </li>
            <li className="flex items-center justify-between">
              <span className="text-slate-600 dark:text-slate-400">Live workers</span>
              <span className="font-mono">
                {data.live_workers >= 0 ? data.live_workers : 'unknown'}
              </span>
            </li>
          </ul>
        </Card>
      </div>

      <Card>
        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Token usage and cost</h2>
        <div className="mt-3 grid gap-4 sm:grid-cols-3 text-sm">
          <div>
            <p className="text-slate-500 dark:text-slate-400">Input tokens</p>
            <p className="font-mono text-lg">{(data.input_tokens ?? 0).toLocaleString()}</p>
          </div>
          <div>
            <p className="text-slate-500 dark:text-slate-400">Output tokens</p>
            <p className="font-mono text-lg">{(data.output_tokens ?? 0).toLocaleString()}</p>
          </div>
          <div>
            <p className="text-slate-500 dark:text-slate-400">Estimated cost</p>
            <p className="font-mono text-lg">{formatCost(data.estimated_cost_usd)}</p>
          </div>
        </div>
      </Card>
    </div>
  );
}

export default function AdminPage() {
  return (
    <RequireAuth>
      <AdminMetrics />
    </RequireAuth>
  );
}
