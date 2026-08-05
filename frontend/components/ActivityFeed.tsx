'use client';

import Link from 'next/link';

import { formatDate } from '@/lib/format';

import { Badge, Card, EmptyState } from './ui';

export interface ActivityItem {
  id: string;
  kind: 'document' | 'analysis';
  title: string;
  href: string;
  at: string;
  status?: string;
  score?: number | null;
}

/**
 * Recent activity, derived from the dashboard payload the API already returns.
 * There is no audit-log endpoint, so this deliberately uses real documents and
 * analyses rather than inventing an activity stream.
 */
export function ActivityFeed({ items }: { items: ActivityItem[] }) {
  if (items.length === 0) {
    return (
      <Card>
        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Recent activity</h2>
        <div className="mt-3">
          <EmptyState
            title="Nothing yet"
            description="Uploads and analyses will appear here as you work."
          />
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Recent activity</h2>
      <ol className="mt-3 space-y-1">
        {items.map((item) => (
          <li key={`${item.kind}-${item.id}`}>
            <Link
              href={item.href}
              className="flex items-center gap-3 rounded-md px-2 py-2 transition
                hover:bg-slate-50 dark:hover:bg-slate-800"
            >
              <span
                aria-hidden="true"
                className={`h-2 w-2 shrink-0 rounded-full ${
                  item.kind === 'analysis' ? 'bg-slate-900 dark:bg-slate-100' : 'bg-slate-400'
                }`}
              />
              <span className="min-w-0 flex-1 truncate text-sm text-slate-900 dark:text-slate-100">
                <span className="text-slate-500 dark:text-slate-400">
                  {item.kind === 'analysis' ? 'Analyzed' : 'Uploaded'}
                </span>{' '}
                {item.title}
              </span>
              {item.status ? (
                <Badge
                  tone={
                    item.status === 'complete'
                      ? 'success'
                      : item.status === 'failed'
                        ? 'danger'
                        : item.status === 'partial'
                          ? 'warning'
                          : 'neutral'
                  }
                >
                  {item.status}
                </Badge>
              ) : null}
              <time
                dateTime={item.at}
                className="hidden shrink-0 text-xs text-slate-500 sm:block dark:text-slate-400"
              >
                {formatDate(item.at)}
              </time>
            </Link>
          </li>
        ))}
      </ol>
    </Card>
  );
}
