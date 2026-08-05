'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { Button, Card, Field, inputClasses } from '@/components/ui';
import { getSupabase } from '@/lib/supabase';

export default function RegisterPage() {
  const router = useRouter();
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const passwordProblem =
    password.length > 0 && password.length < 8 ? 'Use at least 8 characters.' : undefined;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (passwordProblem) return;

    setBusy(true);
    const { data, error: authError } = await getSupabase().auth.signUp({
      email,
      password,
      options: { data: { full_name: fullName } },
    });
    setBusy(false);

    if (authError) {
      setError(authError.message);
      return;
    }
    if (data.session) {
      // A workspace and profile are provisioned by a database trigger on signup.
      router.push('/dashboard');
    } else {
      setNotice('Check your inbox to confirm your email address, then sign in.');
    }
  }

  return (
    <div className="mx-auto max-w-md py-10">
      <Card>
        <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-100">Create your account</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          A private workspace is created for you automatically.
        </p>

        <form onSubmit={submit} className="mt-6 space-y-4">
          <Field label="Full name">
            <input
              type="text"
              required
              autoComplete="name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className={inputClasses}
            />
          </Field>
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
          <Field label="Password" hint="At least 8 characters." error={passwordProblem}>
            <input
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
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
          {notice ? (
            <p className="text-sm text-emerald-700" role="status">
              {notice}
            </p>
          ) : null}

          <Button type="submit" disabled={busy || Boolean(passwordProblem)} className="w-full">
            {busy ? 'Creating account...' : 'Create account'}
          </Button>
        </form>

        <p className="mt-6 text-sm text-slate-600 dark:text-slate-400">
          Already registered?{' '}
          <Link href="/login" className="font-medium underline">
            Sign in
          </Link>
        </p>
      </Card>
    </div>
  );
}
