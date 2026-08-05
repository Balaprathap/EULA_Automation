'use client';

import { useState } from 'react';

import { api } from '@/lib/api';
import { humanizeCategory } from '@/lib/format';
import type { ActionItem, ActionItemPriority, ActionItemStatus } from '@/lib/types';

import { useToast } from './Toast';
import { Badge, Button, InfoTip, controlClasses, inputClasses } from './ui';

const PRIORITY_TONE: Record<ActionItemPriority, 'neutral' | 'warning' | 'danger'> = {
  low: 'neutral',
  medium: 'neutral',
  high: 'warning',
  urgent: 'danger',
};

const STATUSES: ActionItemStatus[] = ['open', 'in_progress', 'completed', 'dismissed'];
const PRIORITIES: ActionItemPriority[] = ['low', 'medium', 'high', 'urgent'];

function dueLabel(item: ActionItem): { text: string; tone: 'neutral' | 'warning' | 'danger' } {
  if (item.date_status === 'unresolved') {
    return { text: 'Date needs review', tone: 'warning' };
  }
  if (!item.due_date) return { text: 'No due date', tone: 'neutral' };
  const due = new Date(item.due_date);
  const days = Math.ceil((due.getTime() - Date.now()) / 86_400_000);
  if (days < 0 && ['open', 'in_progress'].includes(item.status)) {
    return { text: `Overdue by ${Math.abs(days)} day(s)`, tone: 'danger' };
  }
  if (days <= 30) return { text: `Due in ${days} day(s)`, tone: 'warning' };
  return { text: `Due ${item.due_date}`, tone: 'neutral' };
}

export function ActionItemCard({
  item,
  onUpdated,
}: {
  item: ActionItem;
  onUpdated: (updated: ActionItem) => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [dueDate, setDueDate] = useState(item.due_date ?? '');
  const [note, setNote] = useState(item.reviewer_note ?? '');

  async function patch(payload: Parameters<typeof api.updateActionItem>[1], message: string) {
    setBusy(true);
    try {
      onUpdated(await api.updateActionItem(item.id, payload));
      toast.success(message);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'The change could not be saved.');
    } finally {
      setBusy(false);
    }
  }

  const due = dueLabel(item);
  const completed = item.status === 'completed' || item.status === 'dismissed';

  return (
    <article
      className={`print-break-inside-avoid rounded-xl border bg-white p-4 transition dark:bg-slate-900 ${
        completed
          ? 'border-slate-200 opacity-70 dark:border-slate-800'
          : 'border-slate-200 dark:border-slate-800'
      }`}
    >
      <header className="flex flex-wrap items-start gap-2">
        <Badge tone={PRIORITY_TONE[item.priority]}>{item.priority}</Badge>
        <Badge tone="neutral">{humanizeCategory(item.category)}</Badge>
        <Badge tone={due.tone}>{due.text}</Badge>
        {item.status !== 'open' ? <Badge tone="neutral">{item.status}</Badge> : null}
        <span className="ml-auto text-xs text-slate-500 dark:text-slate-400">
          {item.vendor_name ?? item.document_title ?? ''}
        </span>
      </header>

      <h3 className="mt-2 text-sm font-semibold text-slate-900 dark:text-slate-100">
        {item.title}
      </h3>
      <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{item.description}</p>

      {item.date_status === 'unresolved' ? (
        <p className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-200">
          <strong>No calendar date could be determined.</strong> The agreement states a period, not
          a date, and no contract start date is on record. Set the due date manually.
          <InfoTip label="Why the date is unresolved">
            ClauseGuard never invents a date. A clause saying &ldquo;90 days before renewal&rdquo;
            has no calendar meaning without a known renewal date, so the obligation is tracked and
            the date is left for you.
          </InfoTip>
        </p>
      ) : null}

      <button
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="mt-3 rounded text-xs font-medium text-slate-700 underline dark:text-slate-300"
      >
        {expanded ? 'Hide evidence' : 'Show verified evidence'}
      </button>

      {expanded ? (
        <div className="mt-2">
          <blockquote className="border-l-2 border-slate-300 bg-slate-50 py-2 pl-3 pr-2 font-mono text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
            &ldquo;{item.evidence_quote}&rdquo;
          </blockquote>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Verified against the source document
            {item.duration_text ? ` · stated period: ${item.duration_text}` : ''}
          </p>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor={`status-${item.id}`}>
          Status
        </label>
        <select
          id={`status-${item.id}`}
          value={item.status}
          disabled={busy}
          onChange={(e) => patch({ status: e.target.value as ActionItemStatus }, 'Status updated.')}
          className={`${controlClasses} text-xs`}
        >
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {status.replace('_', ' ')}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor={`priority-${item.id}`}>
          Priority
        </label>
        <select
          id={`priority-${item.id}`}
          value={item.priority}
          disabled={busy}
          onChange={(e) =>
            patch({ priority: e.target.value as ActionItemPriority }, 'Priority updated.')
          }
          className={`${controlClasses} text-xs`}
        >
          {PRIORITIES.map((priority) => (
            <option key={priority} value={priority}>
              {priority}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor={`due-${item.id}`}>
          Due date
        </label>
        <input
          id={`due-${item.id}`}
          type="date"
          value={dueDate}
          disabled={busy}
          onChange={(e) => setDueDate(e.target.value)}
          className={`${controlClasses} text-xs`}
        />
        <Button
          variant="secondary"
          disabled={busy || !dueDate || dueDate === item.due_date}
          onClick={() => patch({ due_date: dueDate }, 'Due date set.')}
        >
          Set date
        </Button>
      </div>

      <div className="mt-3">
        <label className="sr-only" htmlFor={`note-${item.id}`}>
          Reviewer note
        </label>
        <textarea
          id={`note-${item.id}`}
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Reviewer note (optional)"
          className={`${inputClasses} mt-0 text-xs`}
        />
        <Button
          variant="ghost"
          disabled={busy || note === (item.reviewer_note ?? '')}
          onClick={() => patch({ reviewer_note: note }, 'Note saved.')}
          className="mt-1"
        >
          Save note
        </Button>
      </div>
    </article>
  );
}
