'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';

import { useAuth } from './AuthProvider';
import { useTheme } from './ThemeProvider';

const LINKS = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/documents', label: 'Documents' },
  { href: '/action-items', label: 'Action items' },
  { href: '/policies', label: 'Policies' },
  { href: '/usage', label: 'Usage' },
  { href: '/admin', label: 'Admin' },
];

function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <button
      onClick={toggle}
      aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
      title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
      className="rounded-md border border-slate-300 px-2 py-1.5 text-sm transition
        hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
    >
      <span aria-hidden="true">{theme === 'dark' ? '☀' : '☾'}</span>
    </button>
  );
}

export function Nav() {
  const { userId, email, signOut } = useAuth();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  const linkClass = (href: string) =>
    `rounded-md px-3 py-2 text-sm font-medium transition ${
      pathname?.startsWith(href)
        ? 'bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-slate-100'
        : 'text-slate-600 hover:bg-slate-50 dark:text-slate-400 dark:hover:bg-slate-800'
    }`;

  return (
    <header className="no-print sticky top-0 z-40 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
      <nav className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-3">
        <Link
          href={userId ? '/dashboard' : '/'}
          className="rounded text-lg font-semibold tracking-tight text-slate-900 dark:text-slate-100"
        >
          Clause<span className="text-slate-500 dark:text-slate-400">Guard</span>
        </Link>

        {userId ? (
          <>
            <ul className="hidden gap-1 md:flex">
              {LINKS.map((link) => (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    aria-current={pathname?.startsWith(link.href) ? 'page' : undefined}
                    className={linkClass(link.href)}
                  >
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>

            <div className="flex items-center gap-2">
              <span className="hidden text-sm text-slate-500 lg:inline dark:text-slate-400">
                {email}
              </span>
              <ThemeToggle />
              <button
                onClick={signOut}
                className="hidden rounded-md border border-slate-300 px-3 py-1.5 text-sm
                  hover:bg-slate-50 sm:block dark:border-slate-700 dark:hover:bg-slate-800"
              >
                Sign out
              </button>
              <button
                onClick={() => setMenuOpen((v) => !v)}
                aria-expanded={menuOpen}
                aria-controls="mobile-menu"
                aria-label="Toggle navigation menu"
                className="rounded-md border border-slate-300 px-2 py-1.5 text-sm md:hidden
                  dark:border-slate-700"
              >
                <span aria-hidden="true">☰</span>
              </button>
            </div>
          </>
        ) : (
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Link
              href="/login"
              className="rounded-md px-3 py-2 text-sm hover:bg-slate-50 dark:hover:bg-slate-800"
            >
              Sign in
            </Link>
            <Link
              href="/register"
              className="rounded-md bg-slate-900 px-3 py-2 text-sm text-white hover:bg-slate-700
                dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
            >
              Create account
            </Link>
          </div>
        )}
      </nav>

      {userId && menuOpen ? (
        <ul
          id="mobile-menu"
          className="border-t border-slate-200 px-4 pb-3 md:hidden dark:border-slate-800"
        >
          {LINKS.map((link) => (
            <li key={link.href}>
              <Link
                href={link.href}
                onClick={() => setMenuOpen(false)}
                className={`${linkClass(link.href)} block`}
              >
                {link.label}
              </Link>
            </li>
          ))}
          <li>
            <button
              onClick={signOut}
              className="mt-1 block w-full rounded-md px-3 py-2 text-left text-sm text-slate-600
                hover:bg-slate-50 dark:text-slate-400 dark:hover:bg-slate-800"
            >
              Sign out
            </button>
          </li>
        </ul>
      ) : null}
    </header>
  );
}
