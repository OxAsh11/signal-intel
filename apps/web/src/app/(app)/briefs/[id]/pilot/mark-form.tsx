'use client';
import { useActionState } from 'react';
import { Button } from '@signal/ui';
import { markPilotVerdict } from '@/app/actions/briefs';

const VERDICTS = ['Confirmed', 'Likely', 'Unverified', 'Contradicted'];

/** Right, or wrong with the correct verdict; optionally adds the account to the Brief's examples. */
export function MarkForm({ verdictId, current }: { verdictId: string; current: string }) {
  const [state, formAction, isPending] = useActionState(markPilotVerdict, {});
  return (
    <form action={formAction} className="flex flex-wrap items-center gap-2">
      <fieldset disabled={isPending} className="contents">
        <input type="hidden" name="verdict" value={verdictId} />
        <Button type="submit" name="mark" value="right" variant="quiet" size="sm">
          Right
        </Button>
        <span className="text-[13px] text-ink-muted">Wrong, it is:</span>
        {VERDICTS.filter((v) => v !== current).map((v) => (
          <Button key={v} type="submit" name="mark" value={v} variant="quiet" size="sm">
            {v}
          </Button>
        ))}
        <label className="flex items-center gap-1 text-[13px] text-ink-muted">
          <input type="checkbox" name="example" /> Add as example
        </label>
      </fieldset>
      {state?.error ? <p className="m-0 w-full text-xs text-danger">{state.error}</p> : null}
    </form>
  );
}
