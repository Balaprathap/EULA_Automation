import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="py-24 text-center">
      <p className="text-sm font-semibold text-slate-500 dark:text-slate-400">404</p>
      <h1 className="mt-2 text-2xl font-bold text-slate-900 dark:text-slate-100">We could not find that page</h1>
      <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
        The link may be out of date, or the item may have been deleted.
      </p>
      <Link
        href="/dashboard"
        className="mt-6 inline-block rounded-md bg-slate-900 px-4 py-2 text-sm text-white hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900"
      >
        Back to the dashboard
      </Link>
    </div>
  );
}
