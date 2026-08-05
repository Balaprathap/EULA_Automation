'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { Button, Card, Field, inputClasses } from '@/components/ui';
import { getSupabase } from '@/lib/supabase';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    const { error: authError } = await getSupabase().auth.signInWithPassword({ email, password });
    setBusy(false);
    if (authError) {
      // Deliberately generic: do not reveal whether the address is registered.
      setError('That email address and password combination was not recognised.');
      return;
    }
    router.push('/dashboard');
  }

  async function signInWithGoogle() {
    setError(null);
    const { error: oauthError } = await getSupabase().auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: `${window.location.origin}/dashboard` },
    });
    if (oauthError) {
      setError('Google sign-in is not configured for this deployment.');
    }
  }

  return (
    <div className="mx-auto max-w-md py-10">
      <Card>
        <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-100">Sign in</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Continue to your compliance workspace.</p>

        <form onSubmit={submit} className="mt-6 space-y-4">
          <Field label="Email">
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={inputClasses}
            />
          </Field>
          <Field label="Password">
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={inputClasses}
            />
          </Field>

          {error ? (
            <p className="text-sm text-red-600" role="alert">
              {error}
            </p>
          ) : null}

          <Button type="submit" disabled={busy} className="w-full">
            {busy ? 'Signing in...' : 'Sign in'}
          </Button>
        </form>

        <div className="my-5 flex items-center gap-3 text-xs text-slate-400 dark:text-slate-500">
          <span className="h-px flex-1 bg-slate-200 dark:bg-slate-700" />
          or
          <span className="h-px flex-1 bg-slate-200 dark:bg-slate-700" />
        </div>

        <Button variant="secondary" onClick={signInWithGoogle} className="w-full">
          Continue with Google
        </Button>

        <p className="mt-6 text-sm text-slate-600 dark:text-slate-400">
          No account yet?{' '}
          <Link href="/register" className="font-medium underline">
            Create one
          </Link>
        </p>
      </Card>
    </div>
  );
}
