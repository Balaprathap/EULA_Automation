import type { RiskBand, Severity } from './types';

export const SEVERITY_ORDER: Severity[] = ['critical', 'high', 'medium', 'low', 'info'];

export function severityClasses(severity: Severity): string {
  const map: Record<Severity, string> = {
    critical: 'bg-red-100 text-red-800 border-red-300',
    high: 'bg-orange-100 text-orange-800 border-orange-300',
    medium: 'bg-yellow-100 text-yellow-800 border-yellow-300',
    low: 'bg-cyan-100 text-cyan-800 border-cyan-300',
    info: 'bg-slate-100 text-slate-700 border-slate-300',
  };
  return map[severity] ?? map.info;
}

export function riskBandClasses(band: RiskBand | null): string {
  const map: Record<RiskBand, string> = {
    high: 'text-red-700',
    elevated: 'text-orange-700',
    moderate: 'text-yellow-700',
    low: 'text-emerald-700',
  };
  return band ? map[band] : 'text-slate-600';
}

export function formatBytes(bytes: number | null): string {
  if (!bytes) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return '-';
  if (usd === 0) return '$0.00';
  if (usd < 0.01) return `<$0.01`;
  return `$${usd.toFixed(2)}`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return '-';
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}

export function humanizeCategory(category: string): string {
  return category
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/**
 * Split text into before / highlighted / after using absolute document offsets.
 *
 * These offsets come from evidence verification, which recomputed them by
 * locating the quote inside the stored chunk. Nothing here trusts an offset the
 * model produced.
 */
export function splitForHighlight(
  text: string,
  start: number,
  end: number,
  offsetBase = 0,
): { before: string; highlight: string; after: string } | null {
  const localStart = start - offsetBase;
  const localEnd = end - offsetBase;
  if (localStart < 0 || localEnd > text.length || localEnd <= localStart) {
    return null;
  }
  return {
    before: text.slice(0, localStart),
    highlight: text.slice(localStart, localEnd),
    after: text.slice(localEnd),
  };
}

/* ------------------------------------------------------------------ *
 * Export + plain-language helpers (frontend-only; no API involvement)
 * ------------------------------------------------------------------ */

/** Plain-language band for a 0-1 model confidence value. */
export function confidenceLabel(confidence: number): 'low' | 'moderate' | 'high' {
  if (confidence >= 0.8) return 'high';
  if (confidence >= 0.5) return 'moderate';
  return 'low';
}

/** RFC-4180-ish CSV escaping. */
function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? '' : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(rows: Record<string, unknown>[], columns: string[]): string {
  const header = columns.map(csvCell).join(',');
  const body = rows.map((row) => columns.map((c) => csvCell(row[c])).join(','));
  return [header, ...body].join('\r\n');
}

/** Trigger a client-side download from data already loaded in the browser. */
export function downloadBlob(content: string, filename: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

/** Copy text to the clipboard, resolving false when the API is unavailable. */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function slugify(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'export';
}
