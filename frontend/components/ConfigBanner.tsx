import { checkEnv } from '@/lib/env';

/**
 * Renders a visible banner when the frontend is misconfigured.
 *
 * Without this, a missing Supabase variable presents as "sign-in silently does
 * nothing", which is a miserable thing to debug.
 */
export function ConfigBanner() {
  const { ok, missing } = checkEnv();
  if (ok) return null;

  return (
    <div
      className="no-print border-b border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100"
      role="alert"
    >
      <strong>Configuration incomplete.</strong> Missing environment{' '}
      {missing.length === 1 ? 'variable' : 'variables'}:{' '}
      <code className="font-mono">{missing.join(', ')}</code>. Copy{' '}
      <code className="font-mono">frontend/.env.example</code> to{' '}
      <code className="font-mono">frontend/.env.local</code>, fill it in, and restart the dev
      server. Authentication and API calls will not work until this is resolved.
    </div>
  );
}
