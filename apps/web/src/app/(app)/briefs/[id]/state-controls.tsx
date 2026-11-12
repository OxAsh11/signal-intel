'use client';
import { useActionState } from 'react';
import { Button } from '@signal/ui';
import { updateBriefState } from '@/app/actions/briefs';

type Brief = { id: string; state: string };

export function StateControls({ brief }: { brief: Brief }) {
  const [state, formAction, isPending] = useActionState(updateBriefState, {});
  const transitions: { label: string; next: string; variant?: 'primary' | 'danger' | 'quiet' }[] =
    brief.state === 'Active' ? [
      { label: 'Pause', next: 'Paused', variant: 'quiet' },
      { label: 'Archive', next: 'Archived', variant: 'danger' },
    ] : brief.state === 'Paused' ? [
      { label: 'Resume', next: 'Active', variant: 'primary' },
      { label: 'Archive', next: 'Archived', variant: 'danger' },
    ] : [];

  if (!transitions.length) return null;

  return (
    <div className="flex gap-2 items-center">
      {transitions.map(t => (
        <form key={t.next} action={formAction} className="inline">
          <fieldset disabled={isPending} className="contents">
            <input type="hidden" name="brief" value={brief.id} />
            <input type="hidden" name="state" value={t.next} />
            <Button type="submit" variant={t.variant ?? 'quiet'} size="sm">{t.label}</Button>
          </fieldset>
        </form>
      ))}
      {state?.error && <p className="text-xs text-danger">{state.error}</p>}
    </div>
  );
}
