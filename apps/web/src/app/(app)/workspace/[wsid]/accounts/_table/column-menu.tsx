'use client';

import { useEffect, useRef, useState } from 'react';
import { setAutoRun, type RunMode } from '@/app/actions/columns';
import type { Column } from './types';

/** The menu's spot: fixed to the viewport, under its button, right-aligned, kept on screen. */
const placeBelow = (r: DOMRect) => ({ top: r.bottom + 4, left: Math.max(8, r.right - 192) });

const ITEMS: { mode: Exclude<RunMode, 'selected'>; label: string }[] = [
  { mode: 'empty', label: 'Run empty' },
  { mode: 'stale', label: 'Run stale' },
  { mode: 'errored', label: 'Run errored' },
];

/**
 * A predicate column's header menu (T4): Run empty, Run stale, Run errored, and for a formula
 * column Pause / Resume auto-run. A run item only opens the run confirm; nothing is queued here.
 * Editor+ only: the table renders no menu for a Viewer.
 */
export function ColumnMenu({ ws, column, onRun }: { ws: string; column: Column; onRun: (mode: Exclude<RunMode, 'selected'>) => void }) {
  // The menu is fixed to the viewport: the table box scrolls sideways and would clip it.
  const [open, setOpen] = useState<{ top: number; left: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const runnable = column.fill_method === 'extract' || column.fill_method === 'formula';
  const formula = column.fill_method === 'formula';

  const isOpen = open !== null;
  useEffect(() => {
    if (!isOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(null);
    };
    // A scroll (the table box sideways, the page, or the browser bringing the button into view)
    // moves the menu with its button. It closes only once the button has left the viewport, and a
    // scroll inside the menu itself is ignored.
    const onScroll = (e: Event) => {
      if (e.target instanceof Node && menuRef.current?.contains(e.target)) return;
      const btn = btnRef.current;
      if (!btn) return setOpen(null);
      const r = btn.getBoundingClientRect();
      if (r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth) return setOpen(null);
      setOpen((o) => (o ? placeBelow(r) : o));
    };
    document.addEventListener('mousedown', onDoc);
    window.addEventListener('scroll', onScroll, true);
    boxRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus({ preventScroll: true });
    return () => {
      document.removeEventListener('mousedown', onDoc);
      window.removeEventListener('scroll', onScroll, true);
    };
    // Only opening or closing re-runs this, not each reposition.
  }, [isOpen]);

  if (!runnable) return null;

  async function toggleAuto() {
    setBusy(true);
    const res = await setAutoRun(ws, column.id, !column.auto_run);
    setBusy(false);
    if (!res.ok) return setError(res.error);
    setError(null);
    setOpen(null);
  }

  const item = 'block w-full px-3 py-1.5 text-left text-[13px] font-normal text-ink hover:bg-surface-sunken focus:bg-surface-sunken focus:outline-none disabled:opacity-50';
  return (
    <div ref={boxRef} className="relative inline-block">
      <button
        ref={btnRef}
        type="button"
        className="ml-1 rounded-sm p-1 text-ink-muted hover:bg-surface-sunken hover:text-ink"
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-label={`Column actions for ${column.label}`}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setOpen((o) => (o ? null : placeBelow(r)));
        }}
      >
        {/* An icon, not a glyph: the header's text stays the column's label. */}
        <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="currentColor" className="block">
          <circle cx="3" cy="8" r="1.5" />
          <circle cx="8" cy="8" r="1.5" />
          <circle cx="13" cy="8" r="1.5" />
        </svg>
      </button>
      {open ? (
        <div
          ref={menuRef}
          role="menu"
          aria-label={`${column.label} actions`}
          style={{ top: open.top, left: open.left }}
          className="fixed z-20 w-48 rounded-md border border-line bg-surface py-1 shadow-lg"
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.stopPropagation();
              setOpen(null);
            }
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
              e.preventDefault();
              const items = [...(boxRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
              const i = items.indexOf(document.activeElement as HTMLElement);
              items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();
            }
          }}
        >
          {ITEMS.map((it) => (
            <button
              key={it.mode}
              type="button"
              role="menuitem"
              className={item}
              onClick={() => {
                setOpen(null);
                onRun(it.mode);
              }}
            >
              {it.label}
            </button>
          ))}
          {formula ? (
            <button type="button" role="menuitem" className={`${item} border-t border-line`} disabled={busy} onClick={() => void toggleAuto()}>
              {column.auto_run === false ? 'Resume auto-run' : 'Pause auto-run'}
            </button>
          ) : null}
          {error ? <p role="alert" className="m-0 px-3 py-1.5 text-[12px] font-normal text-danger">{error}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
