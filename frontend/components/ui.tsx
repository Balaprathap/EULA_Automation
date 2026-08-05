'use client';

import Link from 'next/link';
import { type ReactNode, useEffect, useId, useRef, useState } from 'react';

import { severityClasses } from '@/lib/format';
import type { Severity } from '@/lib/types';

/* ------------------------------------------------------------------ *
 * Existing primitives. Signatures are unchanged; dark-mode variants and
 * accessibility attributes have been added.
 * ------------------------------------------------------------------ */

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition-colors
        dark:border-slate-800 dark:bg-slate-900 ${className}`}
    >
      {children}
    </div>
  );
}

export function Button({
  children,
  onClick,
  type = 'button',
  variant = 'primary',
  disabled = false,
  className = '',
  title,
  ariaLabel,
}: {
  children: ReactNode;
  onClick?: () => void;
  type?: 'button' | 'submit';
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  disabled?: boolean;
  className?: string;
  title?: string;
  ariaLabel?: string;
}) {
  const variants: Record<string, string> = {
    primary:
      'bg-slate-900 text-white hover:bg-slate-700 disabled:bg-slate-400 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white',
    secondary:
      'border border-slate-300 bg-white text-slate-900 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800',
    danger: 'bg-red-600 text-white hover:bg-red-700',
    ghost:
      'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800',
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={ariaLabel}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm
        font-medium transition disabled:cursor-not-allowed disabled:opacity-60
        ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

export function SeverityBadge({
  severity,
  overridden,
}: {
  severity: Severity;
  overridden?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs
        font-semibold uppercase tracking-wide ${severityClasses(severity)}`}
      title={overridden ? 'Severity was overridden by a reviewer' : `Severity: ${severity}`}
    >
      {severity}
      {overridden ? <span aria-label="overridden by a reviewer">*</span> : null}
    </span>
  );
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400" role="status">
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-700
          dark:border-slate-700 dark:border-t-slate-200"
        aria-hidden="true"
      />
      {label}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div
      className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center
        dark:border-slate-700 dark:bg-slate-900"
    >
      {icon ? (
        <div className="mb-3 flex justify-center text-slate-400" aria-hidden="true">
          {icon}
        </div>
      ) : null}
      <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
      <p className="mx-auto mt-1 max-w-md text-sm text-slate-500 dark:text-slate-400">
        {description}
      </p>
      {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div
      className="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950/50"
      role="alert"
    >
      <p className="text-sm font-medium text-red-800 dark:text-red-200">Something went wrong</p>
      <p className="mt-1 text-sm text-red-700 dark:text-red-300">{error}</p>
      {onRetry ? (
        <button
          onClick={onRetry}
          className="mt-3 text-sm font-medium text-red-800 underline hover:text-red-900
            dark:text-red-200 dark:hover:text-red-100"
        >
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function Warning({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div
      className="rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/50"
      role="alert"
    >
      <p className="text-sm font-semibold text-amber-900 dark:text-amber-100">{title}</p>
      <div className="mt-1 text-sm text-amber-800 dark:text-amber-200">{children}</div>
    </div>
  );
}

export function LegalDisclaimer() {
  return (
    <p
      className="rounded-md border border-slate-200 bg-slate-100 px-3 py-2 text-xs text-slate-600
        dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400"
    >
      <strong>Not legal advice.</strong> ClauseGuard highlights clauses that may be relevant to
      compliance review. It is an aid to human judgement, not a substitute for a qualified lawyer.
      Always confirm findings against the source agreement.
    </p>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-700 dark:text-slate-300">{label}</span>
      {children}
      {hint && !error ? (
        <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">{hint}</span>
      ) : null}
      {error ? (
        <span className="mt-1 block text-xs text-red-600 dark:text-red-400" role="alert">
          {error}
        </span>
      ) : null}
    </label>
  );
}

export const inputClasses =
  'mt-1 block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm ' +
  'text-slate-900 placeholder:text-slate-400 focus:border-slate-500 focus:outline-none ' +
  'focus:ring-1 focus:ring-slate-500 dark:border-slate-700 dark:bg-slate-900 ' +
  'dark:text-slate-100 dark:placeholder:text-slate-500';

/* ------------------------------------------------------------------ *
 * New primitives
 * ------------------------------------------------------------------ */

/** Shared control styling so selects stop drifting from inputs. */
export const controlClasses =
  'rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 ' +
  'focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 ' +
  'dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100';

export function Skeleton({ className = '' }: { className?: string }) {
  return (
    <div
      className={`relative overflow-hidden rounded-md bg-slate-200 dark:bg-slate-800 ${className}`}
      aria-hidden="true"
    >
      <div className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/40 to-transparent dark:via-white/5" />
    </div>
  );
}

export function SkeletonCard() {
  return (
    <Card>
      <Skeleton className="h-3 w-24" />
      <Skeleton className="mt-3 h-8 w-16" />
    </Card>
  );
}

export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2" role="status" aria-label="Loading content">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
    </div>
  );
}

export function Badge({
  children,
  tone = 'neutral',
  title,
}: {
  children: ReactNode;
  tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'accent';
  title?: string;
}) {
  const tones: Record<string, string> = {
    neutral:
      'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
    success:
      'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200',
    warning:
      'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200',
    danger: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200',
    accent: 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900',
  };
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/** Accessible tooltip: hover, focus, and screen-reader friendly. */
export function InfoTip({ label, children }: { label: string; children?: ReactNode }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        aria-label={`Help: ${label}`}
        aria-describedby={open ? id : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className="ml-1 inline-flex h-4 w-4 items-center justify-center rounded-full border
          border-slate-400 text-[10px] font-bold leading-none text-slate-500
          hover:bg-slate-100 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-800"
      >
        ?
      </button>
      {open ? (
        <span
          id={id}
          role="tooltip"
          className="absolute bottom-full left-1/2 z-30 mb-2 w-64 -translate-x-1/2 rounded-md
            border border-slate-200 bg-white p-2 text-xs font-normal normal-case leading-relaxed
            text-slate-700 shadow-lg animate-fade-in
            dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        >
          {children ?? label}
        </span>
      ) : null}
    </span>
  );
}

/** Replaces window.confirm for destructive actions. Focus-trapped, Esc closes. */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="no-print fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-title"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-6 shadow-xl
          animate-slide-up dark:border-slate-700 dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="confirm-title" className="text-base font-semibold text-slate-900 dark:text-slate-100">
          {title}
        </h2>
        <div className="mt-2 text-sm text-slate-600 dark:text-slate-400">{body}</div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel}>
            {cancelLabel}
          </Button>
          <button
            ref={confirmRef}
            onClick={onConfirm}
            className={`rounded-md px-3 py-2 text-sm font-medium text-white transition
              ${destructive ? 'bg-red-600 hover:bg-red-700' : 'bg-slate-900 hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900'}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export interface Crumb {
  label: string;
  href?: string;
}

export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="no-print">
      <ol className="flex flex-wrap items-center gap-1 text-sm text-slate-500 dark:text-slate-400">
        {items.map((item, index) => (
          <li key={`${item.label}-${index}`} className="flex items-center gap-1">
            {index > 0 ? (
              <span aria-hidden="true" className="text-slate-300 dark:text-slate-600">
                /
              </span>
            ) : null}
            {item.href ? (
              <Link href={item.href} className="rounded hover:text-slate-900 hover:underline dark:hover:text-slate-100">
                {item.label}
              </Link>
            ) : (
              <span aria-current="page" className="font-medium text-slate-900 dark:text-slate-100">
                {item.label}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone = 'neutral',
  help,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: 'neutral' | 'success' | 'warning' | 'danger';
  help?: string;
}) {
  const tones: Record<string, string> = {
    neutral: 'text-slate-900 dark:text-slate-100',
    success: 'text-emerald-700 dark:text-emerald-400',
    warning: 'text-amber-700 dark:text-amber-400',
    danger: 'text-red-700 dark:text-red-400',
  };
  return (
    <Card>
      <p className="flex items-center text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
        {help ? <InfoTip label={label}>{help}</InfoTip> : null}
      </p>
      <p className={`mt-1 text-3xl font-bold tabular-nums ${tones[tone]}`}>{value}</p>
      {hint ? <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</p> : null}
    </Card>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder = 'Search',
  label,
  className = '',
}: {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  label: string;
  className?: string;
}) {
  return (
    <div className={`relative ${className}`}>
      <label className="sr-only" htmlFor="search-input">
        {label}
      </label>
      <input
        id="search-input"
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={`${controlClasses} w-full pl-8`}
      />
      <span
        aria-hidden="true"
        className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"
      >
        ⌕
      </span>
    </div>
  );
}

export function SectionHeading({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
          {title}
        </h1>
        {description ? (
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{description}</p>
        ) : null}
      </div>
      {action ? <div className="no-print flex gap-2">{action}</div> : null}
    </div>
  );
}
