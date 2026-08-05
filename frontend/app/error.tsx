'use client';

import { useEffect } from 'react';

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Message only - never log anything that could contain document content.
    console.error('Unhandled UI error:', error.message);
  }, [error]);

  return (
    <div className="py-24 text-center">
      <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Something went wrong</h1>
      <p className="mx-auto mt-2 max-w-md text-sm text-slate-600 dark:text-slate-400">
        The page could not be displayed. Your data is unaffected.
      </p>
      {error.digest ? (
        <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">Reference: {error.digest}</p>
      ) : null}
      <button
        onClick={reset}
        className="mt-6 rounded-md bg-slate-900 px-4 py-2 text-sm text-white hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900"
      >
        Try again
      </button>
    </div>
  );
}
