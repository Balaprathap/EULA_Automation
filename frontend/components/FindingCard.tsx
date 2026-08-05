'use client';

import { useState } from 'react';

import { api } from '@/lib/api';
import { confidenceLabel, copyToClipboard, humanizeCategory } from '@/lib/format';
import type { Finding, Severity } from '@/lib/types';

import { useToast } from './Toast';
import { Badge, Button, InfoTip, SeverityBadge, controlClasses, inputClasses } from './ui';

const SEVERITIES: Severity[] = ['info', 'low', 'medium', 'high', 'critical'];

export function FindingCard({
  finding,
  selected,
  onSelect,
  onReviewed,
}: {
  finding: Finding;
  selected: boolean;
  onSelect: () => void;
  onReviewed: (updated: Partial<Finding>) => void;
}) {
  const toast = useToast();
  const [note, setNote] = useState('');
  const [override, setOverride] = useState<Severity | ''>('');
  const [busy, setBusy] = useState(false);
  const [showActions, setShowActions] = useState(false);
  const [expanded, setExpanded] = useState(false);

  async function review(action: string, severity?: string) {
    setBusy(true);
    try {
      await api.reviewFinding(finding.id, {
        action,
        severity,
        note: note.trim() || undefined,
      });
      onReviewed({
        review_status:
          action === 'accept'
            ? 'accepted'
            : action === 'dismiss'
              ? 'dismissed'
              : action === 'escalate'
                ? 'escalated'
                : finding.review_status,
        override_severity: (severity as Severity) ?? finding.override_severity,
        effective_severity: (severity as Severity) ?? finding.effective_severity,
        severity_source: severity ? 'human_override' : finding.severity_source,
      });
      setNote('');
      setOverride('');
      toast.success(severity ? `Severity overridden to ${severity}.` : `Finding ${action}ed.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'The review could not be saved.');
    } finally {
      setBusy(false);
    }
  }

  async function copy(text: string, what: string) {
    const ok = await copyToClipboard(text);
    if (ok) toast.success(`${what} copied.`);
    else toast.error('Copying is not available in this browser.');
  }

  const summaryText = [
    `[${finding.effective_severity.toUpperCase()}] ${humanizeCategory(finding.category)}`,
    finding.plain_summary,
    `Why it matters: ${finding.why_it_matters}`,
    finding.quote ? `Quote: "${finding.quote}"` : null,
    `Confidence ${finding.model_confidence.toFixed(2)} x weight ${finding.severity_weight.toFixed(2)}`,
  ]
    .filter(Boolean)
    .join('\n');

  const quarantined = finding.verification_status === 'quarantined';

  return (
    <article
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
      tabIndex={0}
      role="button"
      aria-pressed={selected}
      aria-label={`${finding.effective_severity} finding in ${humanizeCategory(finding.category)}`}
      className={`print-break-inside-avoid cursor-pointer rounded-xl border bg-white p-4 transition
        dark:bg-slate-900 ${
          selected
            ? 'border-slate-900 ring-1 ring-slate-900 dark:border-slate-100 dark:ring-slate-100'
            : 'border-slate-200 hover:border-slate-400 dark:border-slate-800 dark:hover:border-slate-600'
        }`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <SeverityBadge
          severity={finding.effective_severity}
          overridden={Boolean(finding.override_severity)}
        />
        <span className="text-sm font-medium text-slate-900 dark:text-slate-100">
          {humanizeCategory(finding.category)}
        </span>
        <span className="ml-auto flex flex-wrap items-center gap-2">
          {finding.verification_status === 'verified' ? (
            <Badge tone="success" title="This quote was located in the source document">
              ✓ verified
            </Badge>
          ) : (
            <Badge tone="warning" title="Evidence could not be confirmed">
              {finding.verification_status}
            </Badge>
          )}
          {finding.review_status !== 'pending' ? (
            <Badge tone="neutral">{finding.review_status}</Badge>
          ) : null}
        </span>
      </header>

      <p className="mt-3 text-sm text-slate-900 dark:text-slate-100">{finding.plain_summary}</p>

      <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
        <span className="font-medium text-slate-700 dark:text-slate-300">Why it matters: </span>
        {finding.why_it_matters}
      </p>

      {finding.quote ? (
        <blockquote className="mt-3 border-l-2 border-slate-300 bg-slate-50 py-2 pl-3 pr-2 font-mono text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
          &ldquo;{finding.quote}&rdquo;
        </blockquote>
      ) : null}

      {quarantined ? (
        <p className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-200">
          <strong>Quarantined.</strong>{' '}
          {finding.quarantine_reason ??
            'The quoted text could not be located in the source document.'}{' '}
          It is excluded from the risk score and shown only for transparency.
        </p>
      ) : null}

      {finding.degraded_retrieval ? (
        <p className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-200">
          Retrieval was degraded for this category, so confidence was capped.
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2" onClick={(e) => e.stopPropagation()}>
        <button
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="rounded text-xs font-medium text-slate-700 underline dark:text-slate-300"
        >
          {expanded ? 'Hide details' : 'How this severity was calculated'}
        </button>
        {finding.quote ? (
          <button
            onClick={() => copy(finding.quote ?? '', 'Quote')}
            className="rounded text-xs text-slate-500 underline hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
          >
            Copy quote
          </button>
        ) : null}
        <button
          onClick={() => copy(summaryText, 'Summary')}
          className="rounded text-xs text-slate-500 underline hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
        >
          Copy summary
        </button>
      </div>

      {expanded ? (
        <div
          className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-800/40"
          onClick={(e) => e.stopPropagation()}
        >
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-600 dark:text-slate-400">
            <dt className="flex items-center">
              Model confidence
              <InfoTip label="Model confidence">
                How sure the model is that this clause belongs to this category. It is the only
                number the model contributes — it never chooses severity.
              </InfoTip>
            </dt>
            <dd className="tabular-nums">
              {finding.model_confidence.toFixed(2)}{' '}
              <span className="text-slate-400">({confidenceLabel(finding.model_confidence)})</span>
            </dd>

            <dt className="flex items-center">
              Policy weight
              <InfoTip label="Policy weight">
                How serious your organization considers this category. Set in the policy editor and
                never sent to the model.
              </InfoTip>
            </dt>
            <dd className="tabular-nums">{finding.severity_weight.toFixed(2)}</dd>

            <dt>Threshold</dt>
            <dd className="tabular-nums">{finding.confidence_threshold.toFixed(2)}</dd>

            <dt>Weighted risk</dt>
            <dd className="tabular-nums">{finding.weighted_risk.toFixed(3)}</dd>

            <dt>Machine severity</dt>
            <dd>{finding.machine_severity}</dd>

            <dt className="flex items-center">
              Severity source
              <InfoTip label="Severity source">
                &ldquo;deterministic&rdquo; means the application computed it. &ldquo;human_override&rdquo;
                means a reviewer changed it — the machine value is still preserved.
              </InfoTip>
            </dt>
            <dd>{finding.severity_source}</dd>

            {finding.verification_method ? (
              <>
                <dt className="flex items-center">
                  Verification
                  <InfoTip label="Verification">
                    &ldquo;offset_exact&rdquo; means the quote matched character-for-character.
                    &ldquo;offset_normalized&rdquo; means it matched after whitespace folding, with
                    exact offsets recovered.
                  </InfoTip>
                </dt>
                <dd>{finding.verification_method}</dd>
              </>
            ) : null}
          </dl>
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
            {finding.scoring_explanation}
          </p>
        </div>
      ) : null}

      <div className="no-print mt-3" onClick={(e) => e.stopPropagation()}>
        <button
          onClick={() => setShowActions((v) => !v)}
          aria-expanded={showActions}
          className="rounded text-xs font-medium text-slate-700 underline dark:text-slate-300"
        >
          {showActions ? 'Hide reviewer actions' : 'Review this finding'}
        </button>

        {showActions ? (
          <div className="mt-3 space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-800/40">
            <label className="sr-only" htmlFor={`note-${finding.id}`}>
              Reviewer note
            </label>
            <textarea
              id={`note-${finding.id}`}
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Reviewer note (optional)"
              className={`${inputClasses} mt-0 text-xs`}
            />
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => review('accept')} disabled={busy}>
                Accept
              </Button>
              <Button variant="secondary" onClick={() => review('dismiss')} disabled={busy}>
                Dismiss
              </Button>
              <Button variant="secondary" onClick={() => review('escalate')} disabled={busy}>
                Escalate
              </Button>
              <Button variant="ghost" onClick={() => review('note')} disabled={busy || !note.trim()}>
                Add note only
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="sr-only" htmlFor={`override-${finding.id}`}>
                Override severity
              </label>
              <select
                id={`override-${finding.id}`}
                value={override}
                onChange={(e) => setOverride(e.target.value as Severity | '')}
                className={`${controlClasses} text-xs`}
              >
                <option value="">Override severity…</option>
                {SEVERITIES.map((severity) => (
                  <option key={severity} value={severity}>
                    {severity}
                  </option>
                ))}
              </select>
              <Button
                variant="secondary"
                disabled={busy || !override}
                onClick={() => review('override_severity', override || undefined)}
              >
                Apply override
              </Button>
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              The original machine severity is kept alongside your decision; nothing is overwritten.
            </p>
          </div>
        ) : null}
      </div>
    </article>
  );
}
