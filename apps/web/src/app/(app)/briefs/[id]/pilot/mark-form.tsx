'use client';
import { useActionState, useState } from 'react';
import { Button } from '@signal/ui';
import { markPilotVerdict } from '@/app/actions/briefs';

const VERDICTS = ['Confirmed', 'Likely', 'Unverified', 'Contradicted'];
const REASONS = ['Not a fit', 'Wrong verdict', 'Missing evidence'];

export function MarkForm({ verdictId, current }: { verdictId: string; current: string }) {
  const [state, formAction, isPending] = useActionState(markPilotVerdict, {});
  const [wrong, setWrong] = useState(false);
  return (
    <form action={formAction} className="flex flex-wrap items-center gap-2">
      <fieldset disabled={isPending} className="contents">
        <input type="hidden" name="verdict" value={verdictId} />
        <Button type="submit" name="mark" value="right" variant="quiet" size="sm">
          Right
        </Button>
        <span className="text-[13px] text-ink-muted">Wrong, it is:</span>
        {VERDICTS.filter((v) => v !== current).map((v) => (
          <Button key={v} type="submit" name="mark" value={v} variant="quiet" size="sm" onClick={() => setWrong(true)}>
            {v}
          </Button>
        ))}
        <label className="flex items-center gap-1 text-[13px] text-ink-muted">
          <input type="checkbox" name="example" /> Add as example
        </label>
        {wrong ? (
          <>
            <select name="reason" className="rounded-sm border border-line-strong bg-surface px-2 py-1 text-[13px]">
              {REASONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <input name="note" maxLength={300} placeholder="note (optional)" className="rounded-sm border border-line-strong bg-surface px-2 py-1 text-[13px]" />
          </>
        ) : null}
      </fieldset>
      {state?.error ? <p className="m-0 w-full text-xs text-danger">{state.error}</p> : null}
    </form>
  );
}