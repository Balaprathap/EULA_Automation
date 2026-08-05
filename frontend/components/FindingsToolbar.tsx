'use client';

import { humanizeCategory } from '@/lib/format';
import type { Finding, Severity } from '@/lib/types';

import { Badge, SearchInput, controlClasses } from './ui';

export type SortKey = 'severity' | 'confidence' | 'category';

export interface FindingFilters {
  search: string;
  category: string;
  severity: string;
  reviewStatus: string;
  confidence: string;
  showQuarantined: boolean;
}

export const DEFAULT_FILTERS: FindingFilters = {
  search: '',
  category: '',
  severity: '',
  reviewStatus: '',
  confidence: '',
  showQuarantined: false,
};

const SEVERITY_RANK: Record<Severity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

/** Pure: filter + sort already-loaded findings. No API involvement. */
export function applyFilters(
  findings: Finding[],
  filters: FindingFilters,
  sort: SortKey,
): Finding[] {
  const needle = filters.search.trim().toLowerCase();

  const filtered = findings.filter((finding) => {
    if (!filters.showQuarantined && finding.verification_status === 'quarantined') return false;
    if (filters.category && finding.category !== filters.category) return false;
    if (filters.severity && finding.effective_severity !== filters.severity) return false;
    if (filters.reviewStatus && finding.review_status !== filters.reviewStatus) return false;
    if (filters.confidence) {
      const threshold = Number(filters.confidence);
      if (finding.model_confidence < threshold) return false;
    }
    if (needle) {
      const haystack = [
        finding.plain_summary,
        finding.why_it_matters,
        finding.quote ?? '',
        humanizeCategory(finding.category),
        finding.chunk_heading ?? '',
      ]
        .join(' ')
        .toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  });

  return [...filtered].sort((a, b) => {
    if (sort === 'confidence') return b.model_confidence - a.model_confidence;
    if (sort === 'category') return a.category.localeCompare(b.category);
    const bySeverity =
      SEVERITY_RANK[a.effective_severity] - SEVERITY_RANK[b.effective_severity];
    return bySeverity !== 0 ? bySeverity : b.model_confidence - a.model_confidence;
  });
}

export function FindingsToolbar({
  filters,
  onFiltersChange,
  sort,
  onSortChange,
  categories,
  severityCounts,
  quarantineCount,
  visibleCount,
  totalCount,
  onReset,
  actions,
}: {
  filters: FindingFilters;
  onFiltersChange: (next: FindingFilters) => void;
  sort: SortKey;
  onSortChange: (next: SortKey) => void;
  categories: string[];
  severityCounts: Record<string, number>;
  quarantineCount: number;
  visibleCount: number;
  totalCount: number;
  onReset: () => void;
  actions?: React.ReactNode;
}) {
  const set = <K extends keyof FindingFilters>(key: K, value: FindingFilters[K]) =>
    onFiltersChange({ ...filters, [key]: value });

  const isFiltered =
    filters.search !== '' ||
    filters.category !== '' ||
    filters.severity !== '' ||
    filters.reviewStatus !== '' ||
    filters.confidence !== '';

  return (
    <div className="no-print space-y-3 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-center gap-3">
        <SearchInput
          label="Search findings"
          placeholder="Search summaries, quotes, categories…"
          value={filters.search}
          onChange={(next) => set('search', next)}
          className="min-w-[14rem] flex-1"
        />
        {actions}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="filter-category">
          Filter by category
        </label>
        <select
          id="filter-category"
          value={filters.category}
          onChange={(e) => set('category', e.target.value)}
          className={controlClasses}
        >
          <option value="">All categories</option>
          {categories.map((category) => (
            <option key={category} value={category}>
              {humanizeCategory(category)}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="filter-severity">
          Filter by severity
        </label>
        <select
          id="filter-severity"
          value={filters.severity}
          onChange={(e) => set('severity', e.target.value)}
          className={controlClasses}
        >
          <option value="">All severities</option>
          {(['critical', 'high', 'medium', 'low', 'info'] as Severity[]).map((severity) => (
            <option key={severity} value={severity}>
              {severity} ({severityCounts[severity] ?? 0})
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="filter-confidence">
          Minimum confidence
        </label>
        <select
          id="filter-confidence"
          value={filters.confidence}
          onChange={(e) => set('confidence', e.target.value)}
          className={controlClasses}
        >
          <option value="">Any confidence</option>
          <option value="0.8">High (≥ 0.80)</option>
          <option value="0.5">Moderate (≥ 0.50)</option>
        </select>

        <label className="sr-only" htmlFor="filter-status">
          Filter by review status
        </label>
        <select
          id="filter-status"
          value={filters.reviewStatus}
          onChange={(e) => set('reviewStatus', e.target.value)}
          className={controlClasses}
        >
          <option value="">Any review status</option>
          {['pending', 'accepted', 'dismissed', 'escalated'].map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="sort-key">
          Sort findings
        </label>
        <select
          id="sort-key"
          value={sort}
          onChange={(e) => onSortChange(e.target.value as SortKey)}
          className={controlClasses}
        >
          <option value="severity">Sort: severity</option>
          <option value="confidence">Sort: confidence</option>
          <option value="category">Sort: category</option>
        </select>

        <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
          <input
            type="checkbox"
            checked={filters.showQuarantined}
            onChange={(e) => set('showQuarantined', e.target.checked)}
            className="rounded border-slate-300 dark:border-slate-600"
          />
          Show quarantined ({quarantineCount})
        </label>

        {isFiltered ? (
          <button
            onClick={onReset}
            className="rounded px-2 py-1 text-sm text-slate-600 underline hover:text-slate-900
              dark:text-slate-400 dark:hover:text-slate-100"
          >
            Clear filters
          </button>
        ) : null}

        <span className="ml-auto text-sm text-slate-500 dark:text-slate-400" aria-live="polite">
          <Badge tone={isFiltered ? 'accent' : 'neutral'}>
            {visibleCount} of {totalCount}
          </Badge>
        </span>
      </div>
    </div>
  );
}
