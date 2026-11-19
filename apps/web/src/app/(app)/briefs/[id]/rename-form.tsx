'use client';
import { useActionState, useState } from 'react';
import { Button } from '@signal/ui';
import { renameBrief } from '@/app/actions/briefs';
import { fieldInput } from './chip-field';

/** The Brief's name as the page heading; Editors get a small "Rename" that opens an inline form. */
export function RenameForm({ briefId, name, canEdit }: { briefId: string; name: string; canEdit: boolean }) {
  const [open, setOpen] = useState(false);
  const [state, formAction, pending] = useActionState(
    async (prev: Awaited<ReturnType<typeof renameBrief>>, form: FormData) => {
      const result = await renameBrief(prev, form);
      if ((result as { ok?: boolean }).ok) setOpen(false);
      return result;
    },
    {},
  );

  if (!open) {
    return (
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <h1 className="si-h1 min-w-0 break-words">{name}</h1>
        {canEdit ? <Button size="sm" variant="quiet" onClick={() => setOpen(true)}>Rename</Button> : null}
      </div>
    );
  }
  return (
    <form action={formAction} className="flex min-w-0 flex-col gap-2">
      <fieldset disabled={pending} className="contents">
        <input type="hidden" name="brief" value={briefId} />
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-[13px] text-ink-muted">Brief name</span>
          <input aria-label="Brief name" name="name" defaultValue={name} required maxLength={200} autoFocus className={`${fieldInput} max-w-[520px]`} />
        </label>
        <div className="flex gap-2">
          <Button type="submit" variant="primary" size="sm">Save name</Button>
          <Button type="button" variant="quiet" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
        </div>
        {state?.error ? <p role="alert" className="m-0 text-[13px] text-danger">{state.error}</p> : null}
      </fieldset>
    </form>
  );
}
