'use client';

import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

import { useAuth } from './AuthProvider';
import { Spinner } from './ui';

/**
 * Client-side route guard.
 *
 * This shapes navigation only. Actual authorization is enforced by the API and
 * by Row-Level Security in Postgres - removing this component would change what
 * the UI shows, not what data a user can reach.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { userId, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !userId) router.replace('/login');
  }, [loading, userId, router]);

  if (loading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <Spinner label="Restoring your session" />
      </div>
    );
  }
  if (!userId) return null;
  return <>{children}</>;
}
