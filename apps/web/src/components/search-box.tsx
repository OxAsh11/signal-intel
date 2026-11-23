'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useId, useRef, useState } from 'react';
import { searchAccounts, type SearchHit } from '@/app/actions/views';

/**
 * The top-bar search: finds accounts by name or domain in the workspace (top 8; the server reads
 * % and _ literally) and opens one. Ctrl or Cmd + K focuses it, the arrows move through the
 * results, Enter opens the selected one (the first when none is selected) and Esc closes the list.
 */
export function SearchBox({ ws }: { ws: string }) {
  const router = useRouter();
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const latest = useRef(0);
  const [text, setText] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [state, setState] = useState<'idle' | 'loading' | 'done' | 'error'>('idle');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  // Phone: the field would crowd the user menu in a 390 px top bar. It starts as an icon.
  const [phoneOpen, setPhoneOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPhoneOpen(true);
        requestAnimationFrame(() => {
          inputRef.current?.focus();
          inputRef.current?.select();
        });
      }
    };
    const onDoc = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDoc);
    return () => {
      window.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDoc);
    };
  }, []);

  useEffect(() => {
    const q = text.trim();
    const mine = ++latest.current;
    if (q.length < 2) return;
    const timer = setTimeout(async () => {
      const res = await searchAccounts(ws, q);
      if (mine !== latest.current) return; // a newer query is in flight
      if (res.ok) {
        setHits(res.data);
        setState('done');
      } else {
        setHits([]);
        setState('error');
      }
      setActive(-1);
    }, 200);
    return () => clearTimeout(timer);
  }, [text, ws]);

  const go = (hit: SearchHit | undefined) => {
    if (!hit) return;
    setOpen(false);
    setText('');
    inputRef.current?.blur();
    router.push(hit.href);
  };

  const showList = open && text.trim().length >= 2;
  const openOnPhone = () => {
    setPhoneOpen(true);
    requestAnimationFrame(() => inputRef.current?.focus());
  };
  return (
    <div ref={boxRef} className="relative w-full" role="search" data-search-box="">
      {phoneOpen ? null : (
        <button
          type="button"
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-sm border border-line-strong bg-surface text-ink md:hidden"
          aria-label="Open search"
          aria-expanded={false}
          onClick={openOnPhone}
          data-search-toggle=""
        >
          <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="8.5" cy="8.5" r="5.5" />
            <path d="m13 13 4 4" strokeLinecap="round" />
          </svg>
        </button>
      )}
      <input
        ref={inputRef}
        type="search"
        role="combobox"
        aria-label="Search accounts"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
        autoComplete="off"
        placeholder="Search accounts  Ctrl K"
        value={text}
        maxLength={200}
        className={`${phoneOpen ? 'block' : 'hidden'} w-full min-w-0 rounded-sm border border-line-strong bg-surface px-3 py-1.5 text-[14px] text-ink placeholder:text-ink-faint md:block`}
        onChange={(e) => {
          const next = e.target.value;
          setText(next);
          setOpen(true);
          if (next.trim().length < 2) {
            latest.current++; // drop any answer still on its way
            setHits([]);
            setState('idle');
          } else setState('loading');
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          if (text.trim().length === 0) setPhoneOpen(false);
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' && hits.length) {
            e.preventDefault();
            setOpen(true);
            setActive((a) => (a + 1) % hits.length);
          } else if (e.key === 'ArrowUp' && hits.length) {
            e.preventDefault();
            setActive((a) => (a <= 0 ? hits.length - 1 : a - 1));
          } else if (e.key === 'Enter') {
            e.preventDefault();
            go(hits[active >= 0 ? active : 0]);
          } else if (e.key === 'Escape') {
            setOpen(false);
            setActive(-1);
            inputRef.current?.blur();
          }
        }}
      />
      {showList ? (
        <div id={listId} role="listbox" aria-label="Search results" className="absolute top-full right-0 left-0 z-20 mt-1 overflow-hidden rounded-md border border-line bg-surface shadow-lg">
          {hits.map((h, i) => (
            <Link
              key={h.account_id}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              href={h.href}
              onClick={() => {
                setOpen(false);
                setText('');
              }}
              className={`flex items-baseline justify-between gap-3 px-3 py-2 text-[14px] text-ink no-underline hover:bg-surface-sunken hover:no-underline ${i === active ? 'bg-surface-sunken' : ''}`}
            >
              <span className="truncate">{h.name}</span>
              {h.domain ? <span className="truncate text-[12px] text-ink-muted">{h.domain}</span> : null}
            </Link>
          ))}
          {state === 'done' && hits.length === 0 ? <p className="m-0 px-3 py-2 text-[13px] text-ink-muted">No accounts match</p> : null}
          {state === 'error' ? <p className="m-0 px-3 py-2 text-[13px] text-ink-muted">Search could not load. Try again.</p> : null}
          {state === 'loading' && hits.length === 0 ? <p className="m-0 px-3 py-2 text-[13px] text-ink-muted">Searching…</p> : null}
        </div>
      ) : null}
    </div>
  );
}
