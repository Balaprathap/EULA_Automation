'use client';

import { Badge, InfoTip } from './ui';
import { type Analysis, STAGES } from '@/lib/types';

/**
 * Real analysis progress, driven by the stage the worker has persisted.
 * Nothing here is simulated - if the worker stops, the display stops.
 *
 * Note on "embedding": the backend has no separate `embedding` stage (the DB
 * CHECK constraint does not include one). Embedding runs inside `retrieving`
 * and reports its own timing under stage_timings_ms.embedding, so it is shown
 * as a sub-step rather than a stage. Adding a real stage would require a
 * schema migration.
 */

const STAGE_HELP: Record<string, string> = {
  queued: 'Waiting for a worker to pick the job up.',
  parsing: 'Reading the file and normalizing its text into one canonical form.',
  chunking: 'Splitting the agreement on real clause boundaries, keeping exact character offsets.',
  retrieving: 'Generating embeddings and finding the clauses most relevant to each policy category.',
  extracting: 'Asking the model to identify matching clauses, one category at a time.',
  verifying: 'Checking every quoted clause against the stored source document.',
  scoring: 'Computing severity from your policy weights. The model does not choose severity.',
  complete: 'Finished.',
};

export function AnalysisProgress({
  analysis,
  onRetry,
}: {
  analysis: Analysis;
  onRetry?: () => void;
}) {
  const allStages = [{ key: 'queued' as const, label: 'Queued' }, ...STAGES];
  const currentIndex = allStages.findIndex((stage) => stage.key === analysis.stage);
  const failed = analysis.status === 'failed';
  const percent =
    analysis.categories_total > 0
      ? Math.round((analysis.categories_completed / analysis.categories_total) * 100)
      : 0;

  const embeddingMs = analysis.stage_timings_ms?.embedding;

  return (
    <div className="space-y-4">
      <ol className="flex flex-wrap gap-2" aria-label="Analysis stages">
        {allStages.map((stage, index) => {
          const done = currentIndex > index || analysis.status === 'complete';
          const active = currentIndex === index && !failed;
          return (
            <li
              key={stage.key}
              aria-current={active ? 'step' : undefined}
              className={`flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium transition ${
                done
                  ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200'
                  : active
                    ? 'border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900'
                    : 'border-slate-200 bg-white text-slate-400 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-600'
              }`}
            >
              {done ? <span aria-hidden="true">✓</span> : null}
              {active ? (
                <span
                  className="h-2 w-2 animate-pulse rounded-full bg-current"
                  aria-hidden="true"
                />
              ) : null}
              {stage.label}
              {STAGE_HELP[stage.key] ? (
                <InfoTip label={stage.label}>{STAGE_HELP[stage.key]}</InfoTip>
              ) : null}
            </li>
          );
        })}
      </ol>

      {embeddingMs ? (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Embedding completed in {(embeddingMs / 1000).toFixed(1)}s (part of the retrieving stage).
        </p>
      ) : null}

      {analysis.categories_total > 0 ? (
        <div>
          <div className="flex justify-between text-xs text-slate-500 dark:text-slate-400">
            <span>
              {analysis.categories_completed} of {analysis.categories_total} categories reviewed
            </span>
            <span className="tabular-nums">{percent}%</span>
          </div>
          <div className="mt-1 h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
            <div
              className={`h-full transition-all duration-500 ${failed ? 'bg-red-500' : 'bg-slate-900 dark:bg-slate-100'}`}
              style={{ width: `${percent}%` }}
              role="progressbar"
              aria-valuenow={percent}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Categories reviewed"
            />
          </div>
        </div>
      ) : null}

      {analysis.progress_message ? (
        <p className="text-sm text-slate-600 dark:text-slate-400" aria-live="polite">
          {analysis.progress_message}
        </p>
      ) : null}

      {failed && onRetry ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950/50">
          <p className="text-sm text-red-800 dark:text-red-200">
            {analysis.error_message ?? 'The analysis failed.'}
          </p>
          <button
            onClick={onRetry}
            className="mt-2 rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700"
          >
            Retry analysis
          </button>
        </div>
      ) : null}

      {analysis.categories.length > 0 ? (
        <details className="rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
          <summary className="cursor-pointer text-sm font-medium text-slate-900 dark:text-slate-100">
            Per-category detail ({analysis.categories.length})
          </summary>
          <ul className="mt-3 space-y-1 text-xs">
            {analysis.categories.map((category) => (
              <li key={category.category} className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-slate-900 dark:text-slate-100">
                  {category.category}
                </span>
                <Badge
                  tone={
                    category.status === 'completed'
                      ? 'success'
                      : category.status === 'abstained'
                        ? 'neutral'
                        : 'warning'
                  }
                >
                  {category.status}
                </Badge>
                {category.retrieval_mode && category.retrieval_mode !== 'hybrid' ? (
                  <Badge tone="warning">retrieval: {category.retrieval_mode}</Badge>
                ) : null}
                {category.needs_review_reason ? (
                  <span className="text-slate-500 dark:text-slate-400">
                    — {category.needs_review_reason}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
