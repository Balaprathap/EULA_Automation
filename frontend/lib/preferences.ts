/**
 * Persisted UI preferences (filters, sort, theme).
 *
 * Frontend-only convenience: nothing here affects analysis results, scoring, or
 * anything the backend does. Reads are defensive because localStorage can be
 * unavailable (SSR, private browsing, storage disabled).
 */

const PREFIX = 'clauseguard:';

export function readPreference<T>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(PREFIX + key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function writePreference<T>(key: string, value: T): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* storage full or unavailable - preferences are non-essential */
  }
}

export function clearPreference(key: string): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(PREFIX + key);
  } catch {
    /* ignore */
  }
}

export const PREFERENCE_KEYS = {
  theme: 'theme',
  findingFilters: 'findings.filters',
  findingSort: 'findings.sort',
  documentSearch: 'documents.search',
} as const;
