'use client';
import { useState, useActionState } from 'react';
import { Button } from '@signal/ui';
import { saveBriefDraft } from '@/app/actions/briefs';

type Props = {
  briefId: string;
  fieldName: string;
  label: string;
  required?: boolean;
  currentConfig: Record<string, unknown>;
  renderValue: (config: Record<string, unknown>) => React.ReactNode;
  renderEditor: (config: Record<string, unknown>, onChange: (patch: Record<string, unknown>) => void) => React.ReactNode;
};

export function ChipField({ briefId, fieldName, label, required, currentConfig, renderValue, renderEditor }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(currentConfig);
  const [state, formAction, isPending] = useActionState(saveBriefDraft, {});

  const isEmpty = !currentConfig[fieldName] ||
    (Array.isArray((currentConfig[fieldName] as { positive?: unknown[] } | undefined)?.positive) &&
      ((currentConfig[fieldName] as { positive: unknown[] }).positive.length === 0));

  return (
    <div className={`rounded-md border ${open ? 'border-accent' : 'border-line'} bg-surface`}>
      <button type="button" onClick={() => setOpen(o => !o)}
        className="flex w-full items-center justify-between px-4 py-3 text-left">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{label}</span>
          {required && isEmpty && <span className="text-xs text-caution font-medium">Required</span>}
        </div>
        {!open && <span className="text-sm text-ink-muted">{isEmpty ? 'Not set' : renderValue(currentConfig)}</span>}
      </button>
      {open && (
        <form action={formAction} className="border-t border-line p-4 flex flex-col gap-3">
          <fieldset disabled={isPending} className="contents">
            <input type="hidden" name="brief" value={briefId} />
            <input type="hidden" name="config" value={JSON.stringify(draft)} />
            {renderEditor(draft, patch => setDraft(d => ({ ...d, ...patch })))}
            <div className="flex gap-2">
              <Button type="submit" variant="primary" size="sm">Save</Button>
              <Button type="button" variant="quiet" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
            </div>
            {state?.error && <p className="text-xs text-danger">{state.error}</p>}
          </fieldset>
        </form>
      )}
    </div>
  );
}
