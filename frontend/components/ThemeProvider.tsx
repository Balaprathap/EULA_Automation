'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { PREFERENCE_KEYS, readPreference, writePreference } from '@/lib/preferences';

export type Theme = 'light' | 'dark';

interface ThemeState {
  theme: Theme;
  toggle: () => void;
  setTheme: (next: Theme) => void;
}

const ThemeContext = createContext<ThemeState>({
  theme: 'light',
  toggle: () => {},
  setTheme: () => {},
});

/** Inline script that runs before paint so the theme never flashes. */
export const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem('clauseguard:theme');
    var theme = stored ? JSON.parse(stored) : null;
    if (theme !== 'dark' && theme !== 'light') {
      theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    document.documentElement.classList.toggle('dark', theme === 'dark');
  } catch (e) {}
})();
`;

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>('light');

  useEffect(() => {
    const stored = readPreference<Theme | null>(PREFERENCE_KEYS.theme, null);
    const initial: Theme =
      stored ??
      (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    setThemeState(initial);
    document.documentElement.classList.toggle('dark', initial === 'dark');
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    document.documentElement.classList.toggle('dark', next === 'dark');
    writePreference(PREFERENCE_KEYS.theme, next);
  }, []);

  const toggle = useCallback(() => {
    setTheme(document.documentElement.classList.contains('dark') ? 'light' : 'dark');
  }, [setTheme]);

  const value = useMemo(() => ({ theme, toggle, setTheme }), [theme, toggle, setTheme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext);
}
