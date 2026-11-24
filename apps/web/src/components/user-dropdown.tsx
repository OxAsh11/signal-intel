'use client';

import Link from 'next/link';
import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { createClient } from '@/lib/supabase/client';

type Props = {
  email: string | null;
  name: string | null;
};

export function UserDropdown({ email, name }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();

  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  async function handleSignOut() {
    const supabase = createClient();
    if (supabase) await supabase.auth.signOut();
    router.push('/login');
  }

  const displayName = name ?? email ?? '?';
  const initials = name
    ? name.split(' ').filter(Boolean).map((n) => n[0]).join('').slice(0, 2).toUpperCase()
    : (email?.[0]?.toUpperCase() ?? '?');

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex cursor-pointer items-center gap-1.5 rounded-md border-0 bg-transparent px-2 py-1 hover:bg-surface-sunken"
        aria-expanded={open}
        aria-haspopup="true"
        data-user-menu=""
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-signal-soft text-[11px] font-semibold text-signal-strong">
          {initials}
        </span>
        <span className="max-w-[120px] truncate text-[13px] text-ink-muted">{displayName}</span>
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true" className="shrink-0 text-ink-faint">
          <path d="M2 4L6 8L10 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 min-w-[180px] rounded-md border border-line bg-surface py-1 shadow-lg">
          <p className="m-0 truncate px-3 py-2 text-[12px] text-ink-muted">{email}</p>
          <div className="h-px bg-line" />
          <Link
            href="/settings"
            className="block px-3 py-1.5 text-[13px] text-ink no-underline hover:bg-surface-sunken"
            onClick={() => setOpen(false)}
          >
            Settings
          </Link>
          <div className="h-px bg-line" />
          <button
            type="button"
            onClick={() => void handleSignOut()}
            className="block w-full cursor-pointer border-0 bg-transparent px-3 py-1.5 text-left text-[13px] text-danger hover:bg-surface-sunken"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
