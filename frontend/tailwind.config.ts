import type { Config } from 'tailwindcss';

const config: Config = {
  // Class strategy: the `dark` class is applied to <html> by ThemeProvider,
  // with an inline script in layout.tsx to avoid a flash of the wrong theme.
  darkMode: 'class',
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        severity: {
          info: '#64748b',
          low: '#0891b2',
          medium: '#ca8a04',
          high: '#ea580c',
          critical: '#dc2626',
        },
      },
      keyframes: {
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
      animation: {
        'fade-in': 'fade-in 150ms ease-out',
        'slide-up': 'slide-up 180ms ease-out',
      },
    },
  },
  plugins: [],
};

export default config;
