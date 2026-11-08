'use client';
import { useActionState } from 'react';
import { removeExclusion } from '@/app/actions/briefs';

export function ExclusionRemoveButton({ exclusionId }: { exclusionId: string }) {
  const [state, formAction, isPending] = useActionState(removeExclusion, {});
  return (
    <form action={formAction} className="inline">
      <input type="hidden" name="id" value={exclusionId} />
      <button type="submit" disabled={isPending}
        className="text-xs text-danger hover:underline disabled:opacity-50">
        {isPending ? '…' : 'Remove'}
      </button>
      {state?.error && <span className="ml-2 text-xs text-danger">{state.error}</span>}
    </form>
  );
}
