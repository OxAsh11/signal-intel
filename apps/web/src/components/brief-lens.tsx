'use client';

import { useEffect, useRef, useState, useTransition } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';
import { setBriefLens } from '@/app/actions/lens';
import { inputClass } from './forms';

export type LensOption = { id: string; name: string; state: 'Draft' | 'Pilot' | 'Active' | 'Paused' };

/** Pilot and Paused Briefs carry their state; Draft and Active read by name alone. */
const optionLabel = (b: LensOption) => (b.state === 'Pilot' || b.state === 'Paused' ? `${b.name} · ${b.state}` : b.name);

/**
 * The Brief lens selector at the top of the sidebar: "All Briefs" or one Brief, and an Apply
 * button. `setBriefLens` sets the workspace's lens cookie and keeps the user on the current page.
 *
 * A change applies at once only when it came from a pointer (a click or tap in the list, one
 * step). From the keyboard, arrowing through the options fires a change per key on Windows, so
 * there a change only moves the selection: Enter on the select, or the Apply button, applies it
 * (WCAG 3.2.2, no change of context on input).
 *
 * A valid `?brief=` in the URL outranks the cookie, so it is what the selector shows; choosing
 * then lands on the same page without it.
 */
export function BriefLens({ briefs, value, compact }: { briefs: LensOption[]; value: string; compact?: boolean }) {
  const params = useSearchParams();
  const fromUrl = params.get('brief');
  const urlValid = fromUrl === 'all' || briefs.some((b) => b.id === fromUrl);
  const current = urlValid && fromUrl ? fromUrl : value;
  return <LensForm briefs={briefs} current={current} compact={compact} />;
}

function LensForm({ briefs, current, compact }: { briefs: LensOption[]; current: string; compact?: boolean }) {
  const pathname = usePathname();
  const params = useSearchParams();
  const [choice, setChoice] = useState(current);
  // When the server's value changes (applied, another workspace, a ?brief= link), the selection
  // follows it. Adjusted during render rather than by remounting, so focus stays on the control.
  const [seen, setSeen] = useState(current);
  if (seen !== current) {
    setSeen(current);
    setChoice(current);
  }
  const fromPointer = useRef(false);
  const [, startTransition] = useTransition();

  let next = '';
  if (params.has('brief')) {
    const rest = new URLSearchParams(params.toString());
    rest.delete('brief');
    next = rest.size ? `${pathname}?${rest.toString()}` : pathname;
  }

  // Submitted by hand rather than through <form action>: React resets a form after its action,
  // which would knock the controlled select off the value it shows.
  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    startTransition(async () => {
      const result = await setBriefLens(form);
      // Refused (e.g. the Brief was archived meanwhile): show what the server still has.
      if (result && !result.ok) setChoice(current);
    });
  }

  const selectCls = compact
    ? 'h-[30px] cursor-pointer rounded-md border border-signal/30 bg-signal-soft px-3 text-[13px] font-medium text-signal focus-visible:outline-none focus-visible:shadow-[var(--focus-ring)]'
    : `${inputClass} w-full min-w-0 flex-1 truncate`;

  return (
    <form onSubmit={onSubmit} className="flex min-w-0 items-center gap-1.5">
      {next ? <input type="hidden" name="next" value={next} /> : null}
      <select
        name="brief"
        aria-label="Brief lens"
        value={choice}
        className={selectCls}
        onPointerDown={() => {
          fromPointer.current = true;
        }}
        onKeyDown={(e) => {
          fromPointer.current = false;
          if (e.key === 'Enter') {
            e.preventDefault();
            e.currentTarget.form?.requestSubmit();
          }
        }}
        onChange={(e) => {
          setChoice(e.currentTarget.value);
          if (fromPointer.current) {
            fromPointer.current = false;
            e.currentTarget.form?.requestSubmit();
          }
        }}
      >
        <option value="all">All Briefs</option>
        {briefs.map((b) => (
          <option key={b.id} value={b.id}>
            {optionLabel(b)}
          </option>
        ))}
      </select>
      {!compact && (
        <div className="flex-none">
          <button type="submit" aria-label="Apply Brief lens" className="si-btn si-btn-sm">
            Apply
          </button>
        </div>
      )}
    </form>
  );
}

/** On a Brief's page: makes that Brief the lens (once per Brief, unless it already is). */
export function LensOnOpen({ briefId, already }: { briefId: string; already: boolean }) {
  useEffect(() => {
    if (already) return;
    const form = new FormData();
    form.set('brief', briefId);
    void setBriefLens(form);
    // Only on opening a different Brief, not on every re-render of the page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [briefId]);
  return null;
}
