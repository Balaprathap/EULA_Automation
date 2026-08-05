/**
 * Frontend configuration.
 *
 * Only values that are safe to ship to a browser appear here. The Supabase
 * service-role key and the Anthropic key are backend-only and are deliberately
 * absent - if one ever appears in this file, it is a security bug.
 */

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `Missing environment variable ${name}. Copy frontend/.env.example to ` +
        `frontend/.env.local and fill it in.`,
    );
  }
  return value;
}

export const env = {
  supabaseUrl: required('NEXT_PUBLIC_SUPABASE_URL', process.env.NEXT_PUBLIC_SUPABASE_URL),
  supabaseAnonKey: required(
    'NEXT_PUBLIC_SUPABASE_ANON_KEY',
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  ),
  apiBaseUrl: (process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, ''),
};

/** Validates configuration without throwing - used by the config banner. */
export function checkEnv(): { ok: boolean; missing: string[] } {
  const missing: string[] = [];
  if (!process.env.NEXT_PUBLIC_SUPABASE_URL) missing.push('NEXT_PUBLIC_SUPABASE_URL');
  if (!process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY) missing.push('NEXT_PUBLIC_SUPABASE_ANON_KEY');
  return { ok: missing.length === 0, missing };
}
