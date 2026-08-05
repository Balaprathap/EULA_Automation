'use client';

import Link from 'next/link';

import type { ActionItemSummary } from '@/lib/types';

import { Card, InfoTip } from './ui';

/** Dashboard widget: urgent, overdue and unresolved-date counts. */
export function ActionItemWidget({ summary }: { summary: ActionItemSummary | null }) {
  if (!summary) return null;

  const cells = [
    { label: 'Open', value: summary.open_count, tone: '' },
    { label: 'Overdue', value: summary.overdue_count, tone: 'text-red-700 dark:text-red-400' },
    { label: 'Due soon', value: summary.due_soon_count, tone: 'text-amber-700 dark:text-amber-400' },
    { label: 'Urgent', value: summary.urgent_count, tone: 'text-red-700 dark:text-red-400' },
  ];

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="flex items-center text-sm font-semibold text-slate-900 dark:text-slate-100">
          Action items
          <InfoTip label="Action items">
            Obligations derived from verified findings only — cancellation deadlines, renewal
            notice windows, retention periods. Quarantined findings never produce action items.
          </InfoTip>
        </h2>
        <Link
          href="/action-items"
          className="rounded text-xs font-medium text-slate-600 underline hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
        >
          View all
        </Link>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {cells.map((cell) => (
          <div key={cell.label}>
            <dt className="text-xs text-slate-500 dark:text-slate-400">{cell.label}</dt>
            <dd className={`text-2xl font-bold tabular-nums ${cell.tone}`}>{cell.value}</dd>
          </div>
        ))}
      </dl>

      {summary.unresolved_date_count > 0 ? (
        <p className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-200">
          {summary.unresolved_date_count} item(s) state a period but no calendar date. Set a due
          date so they can be tracked.
        </p>
      ) : null}
    </Card>
  );
}
