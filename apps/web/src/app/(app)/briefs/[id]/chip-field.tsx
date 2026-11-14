'use client';
import { useState, useActionState } from 'react';
import { Button } from '@signal/ui';
import { saveBriefDraft } from '@/app/actions/briefs';

type Props = {
  briefId: string;
  fieldName: string;
  label: string;
  required?: boolean;
  disabled?: boolean;
  isEmpty?: (config: Record<string, unknown>) => boolean;
  currentConfig: Record<string, unknown>;
  renderValue: (config: Record<string, unknown>) => React.ReactNode;
  renderEditor: (config: Record<string, unknown>, onChange: (patch: Record<string, unknown>) => void) => React.ReactNode;
};

/** Shared input style for the Brief's field editors. */
export const fieldInput = 'w-full rounded-sm border border-line-strong bg-surface px-3 py-2 text-[14px] text-ink';

/**
 * One part of the AI's draft, shown as a readable statement. "Change" opens its editor; saving
 * writes the draft (AI-first: the AI fills it, the person checks it).
 */
export function ChipField({ briefId, fieldName, label, required, disabled, isEmpty: isEmptyFn, currentConfig, renderValue, renderEditor }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(currentConfig);
  // A successful save closes the editor.
  const [state, formAction, isPending] = useActionState(
    async (prev: Awaited<ReturnType<typeof saveBriefDraft>>, form: FormData) => {
      const result = await saveBriefDraft(prev, form);
      if ((result as { ok?: boolean }).ok) setOpen(false);
      return result;
    },
    {},
  );

  // Opening starts from the latest server state, so a stale draft never overwrites it.
  const openEditor = () => {
    if (disabled) return;
    setDraft(currentConfig);
    setOpen(true);
  };

  const isEmpty = isEmptyFn
    ? isEmptyFn(currentConfig)
    : !currentConfig[fieldName] ||
      (Array.isArray((currentConfig[fieldName] as { positive?: unknown[] } | undefined)?.positive) &&
        (currentConfig[fieldName] as { positive: unknown[] }).positive.length === 0);

  return (
    <section className={`si-card ${open ? 'border-ink' : ''}`} aria-label={label}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h2 className="si-label m-0">{label}</h2>
          {required && isEmpty ? <span className="si-ftag si-fam-mustard">Needed to start the pilot</span> : null}
        </div>
        {!open && !disabled ? (
          <Button size="sm" onClick={openEditor}>
            Change
          </Button>
        ) : null}
      </div>
      {!open ? (
        <div className="text-[15px] leading-[1.6] text-ink">{isEmpty ? <span className="text-ink-muted">Not drafted yet.</span> : renderValue(currentConfig)}</div>
      ) : (
        <form action={formAction} className="flex flex-col gap-3">
          <fieldset disabled={isPending} className="contents">
            <input type="hidden" name="brief" value={briefId} />
            <input type="hidden" name="config" value={JSON.stringify({ ...currentConfig, [fieldName]: (draft as Record<string, unknown>)[fieldName] })} />
            {renderEditor(draft, (patch) => setDraft((d) => ({ ...d, ...patch })))}
            <div className="flex gap-2">
              <Button type="submit" variant="primary" size="sm">Save</Button>
              <Button type="button" variant="quiet" size="sm" onClick={() => { setDraft(currentConfig); setOpen(false); }}>
                Cancel
              </Button>
            </div>
            {state?.error ? <p className="m-0 text-[13px] text-danger">{state.error}</p> : null}
          </fieldset>
        </form>
      )}
    </section>
  );
}
