'use client';

import { useEffect } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';
import { setBriefLens } from '@/app/actions/lens';
import { inputClass } from './forms';

export type LensOption = { id: string; name: string; state: 'Draft' | 'Pilot' | 'Active' | 'Paused' };

/** Pilot and Paused Briefs carry their state; Draft and Active read by name alone. */
const optionLabel = (b: LensOption) => (b.state === 'Pilot' || b.state === 'Paused' ? `${b.name} · ${b.state}` : b.name);

/**
 * The Brief lens selector at the top of the sidebar: "All Briefs" or one Brief. Choosing submits
 * `setBriefLens`, which sets the workspace's lens cookie and keeps the user on the current page.
 * A valid `?brief=` in the URL outranks the cookie, so it is what the selector shows; choosing
 * then lands on the same page without it.
 */
export function BriefLens({ briefs, value }: { briefs: LensOption[]; value: string }) {
  const pathname = usePathname();
  const params = useSearchParams();
  const fromUrl = params.get('brief');
  const urlValid = fromUrl === 'all' || briefs.some((b) => b.id === fromUrl);
  const current = urlValid && fromUrl ? fromUrl : value;

  let next = '';
  if (params.has('brief')) {
    const rest = new URLSearchParams(params.toString());
    rest.delete('brief');
    next = rest.size ? `${pathname}?${rest.toString()}` : pathname;
  }

  return (
    <form action={setBriefLens} className="min-w-0">
      {next ? <input type="hidden" name="next" value={next} /> : null}
      <select
        key={current}
        name="brief"
        aria-label="Brief lens"
        defaultValue={current}
        className={`${inputClass} w-full min-w-0 truncate`}
        onChange={(e) => e.currentTarget.form?.requestSubmit()}
      >
        <option value="all">All Briefs</option>
        {briefs.map((b) => (
          <option key={b.id} value={b.id}>
            {optionLabel(b)}
          </option>
        ))}
      </select>
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
