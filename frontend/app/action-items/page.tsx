'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { ActionItemCard } from '@/components/ActionItemCard';
import { RequireAuth } from '@/components/RequireAuth';
import {
  Badge,
  Breadcrumbs,
  Button,
  Card,
  EmptyState,
  ErrorState,
  SearchInput,
  SectionHeading,
  SkeletonRows,
  StatCard,
  controlClasses,
} from '@/components/ui';
import { api } from '@/lib/api';
import { downloadBlob, humanizeCategory, toCsv } from '@/lib/format';
import { readPreference, writePreference } from '@/lib/preferences';
import type { ActionItem, ActionItemSummary } from '@/lib/types';

const FILTER_KEY = 'actionItems.filters';

interface Filters {
  status: string;
  priority: string;
  category: string;
  due: string;
  sort: string;
  search: string;
}

const DEFAULTS: Filters = {
  status: 'open',
  priority: '',
  category: '',
  due: '',
  sort: 'due_date',
  search: '',
};

const CSV_COLUMNS = [
  'title',
  'category',
  'obligation_type',
  'priority',
  'status',
  'due_date',
  'date_status',
  'duration_text',
  'document_title',
  'vendor_name',
  'evidence_quote',
];

function ActionItemsWorkspace() {
  const [items, setItems] = useState<ActionItem[]>([]);
  const [summary, setSummary] = useState<ActionItemSummary | null>(null);
  const [filters, setFilters] = useState<Filters>(DEFAULTS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setFilters(readPreference<Filters>(FILTER_KEY, DEFAULTS));
  }, []);

  const update = useCallback((next: Filters) => {
    setFilters(next);
    writePreference(FILTER_KEY, next);
  }, []);

  const load = useCallback(async (current: Filters) => {
    setLoading(true);
    setError(null);
    try {
      const [page, counts] = await Promise.all([
        api.listActionItems({
          status: current.status || undefined,
          priority: current.priority || undefined,
          category: current.category || undefined,
          due: current.due || undefined,
          sort: current.sort,
          limit: 200,
        }),
        api.getActionItemSummary(),
      ]);
      setItems(page.items);
      setSummary(counts);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load action items.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(filters);
  }, [filters, load]);

  const categories = useMemo(
    () => Array.from(new Set(items.map((i) => i.category))).sort(),
    [items],
  );

  const visible = useMemo(() => {
    const needle = filters.search.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((item) =>
      [item.title, item.description, item.evidence_quote, item.document_title ?? '', item.vendor_name ?? '']
        .join(' ')
        .toLowerCase()
        .includes(needle),
    );
  }, [items, filters.search]);

  function exportCsv() {
    const rows = visible.map((item) => ({ ...item }) as unknown as Record<string, unknown>);
    downloadBlob(toCsv(rows, CSV_COLUMNS), 'clauseguard-action-items.csv', 'text/csv');
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Dashboard', href: '/dashboard' }, { label: 'Action items' }]} />

      <SectionHeading
        title="Action items"
        description="Obligations derived from verified findings. Quarantined findings never produce an action item."
        action={
          visible.length > 0 ? (
            <Button variant="secondary" onClick={exportCsv}>
              Export CSV
            </Button>
          ) : undefined
        }
      />

      {summary ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Open" value={summary.open_count} />
          <StatCard
            label="Overdue"
            value={summary.overdue_count}
            tone={summary.overdue_count > 0 ? 'danger' : 'neutral'}
          />
          <StatCard
            label="Due in 30 days"
            value={summary.due_soon_count}
            tone={summary.due_soon_count > 0 ? 'warning' : 'neutral'}
          />
          <StatCard
            label="Date needs review"
            value={summary.unresolved_date_count}
            tone={summary.unresolved_date_count > 0 ? 'warning' : 'success'}
            help="The agreement states a period (e.g. 90 days) but no calendar date could be derived, because no contract start date is on record."
          />
        </div>
      ) : null}

      <Card className="flex flex-wrap items-center gap-2">
        <SearchInput
          label="Search action items"
          placeholder="Search titles, evidence, vendors…"
          value={filters.search}
          onChange={(search) => update({ ...filters, search })}
          className="min-w-[13rem] flex-1"
        />

        <label className="sr-only" htmlFor="ai-status">Status</label>
        <select
          id="ai-status"
          value={filters.status}
          onChange={(e) => update({ ...filters, status: e.target.value })}
          className={controlClasses}
        >
          <option value="">Any status</option>
          {['open', 'in_progress', 'completed', 'dismissed'].map((s) => (
            <option key={s} value={s}>{s.replace('_', ' ')}</option>
          ))}
        </select>

        <label className="sr-only" htmlFor="ai-priority">Priority</label>
        <select
          id="ai-priority"
          value={filters.priority}
          onChange={(e) => update({ ...filters, priority: e.target.value })}
          className={controlClasses}
        >
          <option value="">Any priority</option>
          {['urgent', 'high', 'medium', 'low'].map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>

        <label className="sr-only" htmlFor="ai-category">Category</label>
        <select
          id="ai-category"
          value={filters.category}
          onChange={(e) => update({ ...filters, category: e.target.value })}
          className={controlClasses}
        >
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>{humanizeCategory(c)}</option>
          ))}
        </select>

        <label className="sr-only" htmlFor="ai-due">Due</label>
        <select
          id="ai-due"
          value={filters.due}
          onChange={(e) => update({ ...filters, due: e.target.value })}
          className={controlClasses}
        >
          <option value="">Any due date</option>
          <option value="overdue">Overdue</option>
          <option value="soon">Due in 30 days</option>
          <option value="unresolved">Date needs review</option>
        </select>

        <label className="sr-only" htmlFor="ai-sort">Sort</label>
        <select
          id="ai-sort"
          value={filters.sort}
          onChange={(e) => update({ ...filters, sort: e.target.value })}
          className={controlClasses}
        >
          <option value="due_date">Sort: due date</option>
          <option value="priority">Sort: priority</option>
          <option value="created_at">Sort: newest</option>
        </select>

        <span className="ml-auto">
          <Badge tone="neutral">{visible.length} shown</Badge>
        </span>
      </Card>

      {error ? <ErrorState error={error} onRetry={() => void load(filters)} /> : null}
      {loading ? <SkeletonRows rows={4} /> : null}

      {!loading && !error && visible.length === 0 ? (
        <EmptyState
          title="No action items"
          description="Action items are created automatically from verified findings when an analysis completes. Run an analysis, or widen the filters."
          action={
            <Link href="/documents/upload">
              <Button>Analyze an agreement</Button>
            </Link>
          }
        />
      ) : null}

      {!loading && visible.length > 0 ? (
        <div className="space-y-3">
          {visible.map((item) => (
            <ActionItemCard
              key={item.id}
              item={item}
              onUpdated={(updated) =>
                setItems((current) => current.map((i) => (i.id === updated.id ? updated : i)))
              }
            />
          ))}
        </div>
      ) : null}

      <p className="rounded-md border border-slate-200 bg-slate-100 px-3 py-2 text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
        <strong>Not legal advice.</strong> Action items are derived from clauses found in your
        agreements. Confirm every deadline against the source contract before relying on it.
      </p>
    </div>
  );
}

export default function ActionItemsPage() {
  return (
    <RequireAuth>
      <ActionItemsWorkspace />
    </RequireAuth>
  );
}
