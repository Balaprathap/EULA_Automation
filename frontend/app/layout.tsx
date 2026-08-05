import type { Metadata } from 'next';

import { AuthProvider } from '@/components/AuthProvider';
import { ConfigBanner } from '@/components/ConfigBanner';
import { Nav } from '@/components/Nav';
import { THEME_INIT_SCRIPT, ThemeProvider } from '@/components/ThemeProvider';
import { ToastProvider } from '@/components/Toast';

import './globals.css';

export const metadata: Metadata = {
  title: 'ClauseGuard - Automated EULA Compliance Extraction',
  description:
    'Analyze EULAs, terms of service, and SaaS agreements for compliance-relevant clauses, ' +
    'with verified source evidence for every finding.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Applies the stored theme before first paint to avoid a flash. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-screen">
        <a href="#main-content" className="skip-link">
          Skip to main content
        </a>
        <ThemeProvider>
          <ToastProvider>
            <ConfigBanner />
            <AuthProvider>
              <Nav />
              <main id="main-content" className="mx-auto max-w-7xl px-4 py-8">
                {children}
              </main>
              <footer className="no-print mx-auto max-w-7xl px-4 pb-10 pt-4 text-xs text-slate-500 dark:text-slate-500">
                ClauseGuard - Automated EULA Compliance Extraction. Informational only; not legal
                advice.
              </footer>
            </AuthProvider>
          </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
