'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { RequireAuth } from '@/components/RequireAuth';
import { Button, Card, EmptyState, ErrorState, Spinner } from '@/components/ui';
import { api } from '@/lib/api';
import { formatDate } from '@/lib/format';
import type { Policy } from '@/lib/types';

function PolicyList() {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPolicies(await api.listPolicies());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load policies.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function mutate(id: string, payload: Record<string, unknown>) {
    setBusy(id);
    setError(null);
    try {
      await api.updatePolicy(id, payload);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The change could not be saved.');
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <Spinner label="Loading policies" />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Compliance policies</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Categories, severity weights, and thresholds. These values drive scoring and are never
            sent to the model.
          </p>
        </div>

        <Link href="/policies/new">
          <Button>Create policy with AI ✦</Button>
        </Link>
      </div>

      {error ? <ErrorState error={error} onRetry={load} /> : null}

      {policies.length === 0 ? (
        <EmptyState
          title="No policies yet"
          description="Seed the default policy with `make seed`, or ask an administrator to create one."
        />
      ) : (
        <div className="space-y-3">
          {policies.map((policy) => (
            <Card key={policy.id} className="flex flex-wrap items-center gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    href={`/policies/${policy.id}`}
                    className="font-medium hover:underline"
                  >
                    {policy.name}
                  </Link>
                  <span className="text-xs text-slate-500 dark:text-slate-400">v{policy.version}</span>
                  {policy.is_default ? (
                    <span className="rounded-full bg-slate-900 px-2 py-0.5 text-xs text-white dark:bg-slate-100 dark:text-slate-900">
                      default
                    </span>
                  ) : null}
                  {!policy.is_active ? (
                    <span className="rounded-full bg-slate-200 px-2 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                      inactive
                    </span>
                  ) : null}
                </div>
                <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                  {policy.rule_count} categories - updated {formatDate(policy.updated_at)}
                </p>
              </div>

              <div className="flex flex-wrap gap-2">
                <Link href={`/policies/${policy.id}`}>
                  <Button variant="secondary">Edit categories</Button>
                </Link>
                {!policy.is_default ? (
                  <Button
                    variant="ghost"
                    disabled={busy === policy.id}
                    onClick={() => mutate(policy.id, { is_default: true })}
                  >
                    Make default
                  </Button>
                ) : null}
                <Button
                  variant="ghost"
                  disabled={busy === policy.id}
                  onClick={() => mutate(policy.id, { is_active: !policy.is_active })}
                >
                  {policy.is_active ? 'Deactivate' : 'Activate'}
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <p className="text-xs text-slate-500 dark:text-slate-400">
        Creating and editing policies requires an administrator or owner role. The API enforces this
        independently of what this page shows.
      </p>
    </div>
  );
}

export default function PoliciesPage() {
  return (
    <RequireAuth>
      <PolicyList />
    </RequireAuth>
  );
}
