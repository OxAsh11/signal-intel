'use client';

import { useEffect, useState } from 'react';

const KEY = 'si-theme';
type Theme = 'light' | 'dark';

/** Reads localStorage only on the client; returns 'light' on the server. */
function getStoredTheme(): Theme {
  if (typeof window === 'undefined') return 'light';
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === 'dark' || saved === 'light') return saved;
  } catch {
    // ignore
  }
  return 'light';
}

export function ThemeToggle() {
  // Lazy initializer runs client-side only; on the server it returns 'light' (hydration-safe).
  const [theme, setTheme] = useState<Theme>(getStoredTheme);

  // Sync state → DOM and localStorage (the external system).  No setState here.
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      // ignore
    }
  }, [theme]);

  function toggle() {
    setTheme((prev) => (prev === 'light' ? 'dark' : 'light'));
  }

  return (
    <button
      type="button"
      onClick={toggle}
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-line bg-surface hover:bg-surface-sunken"
      aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
      title={theme === 'light' ? 'Dark mode' : 'Light mode'}
    >
      {theme === 'dark' ? (
        /* Sun icon — click to go light */
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <circle cx="8" cy="8" r="3" stroke="currentColor" strokeWidth="1.5" />
          <line x1="8" y1="1" x2="8" y2="2.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <line x1="8" y1="13.5" x2="8" y2="15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <line x1="1" y1="8" x2="2.5" y2="8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <line x1="13.5" y1="8" x2="15" y2="8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <line x1="2.93" y1="2.93" x2="4.05" y2="4.05" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <line x1="11.95" y1="11.95" x2="13.07" y2="13.07" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <line x1="13.07" y1="2.93" x2="11.95" y2="4.05" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <line x1="4.05" y1="11.95" x2="2.93" y2="13.07" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      ) : (
        /* Moon icon — click to go dark */
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M13.5 9.5A6 6 0 0 1 6.5 2.5a6 6 0 1 0 7 7z"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </button>
  );
}
