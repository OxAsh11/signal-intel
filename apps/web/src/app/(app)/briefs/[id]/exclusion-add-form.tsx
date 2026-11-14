'use client';
import { useActionState } from 'react';
import { addExclusion } from '@/app/actions/briefs';

export function ExclusionAddForm({ briefId }: { briefId: string }) {
  const [state, formAction, isPending] = useActionState(addExclusion, {});
  return (
    <form action={formAction} className="flex flex-col gap-2">
      <input type="hidden" name="brief" value={briefId} />
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Type</span>
          <select name="value_type" required
            className="rounded border border-line bg-surface px-2 py-1 text-sm">
            <option value="name">Name</option>
            <option value="domain">Domain</option>
            <option value="entity">Entity ID</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Pattern (name / domain)</span>
          <input name="pattern" placeholder="e.g. acme.com"
            className="rounded border border-line bg-surface px-2 py-1 text-sm" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Entity ID (entity type)</span>
          <input name="entity_id" placeholder="UUID"
            className="rounded border border-line bg-surface px-2 py-1 font-mono text-sm" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Reason (optional)</span>
          <input name="reason" placeholder="Competitor"
            className="rounded border border-line bg-surface px-2 py-1 text-sm" />
        </label>
        <button type="submit" disabled={isPending}
          className="si-btn si-btn-primary si-btn-sm">
          Add
        </button>
      </div>
      {state?.error && <p className="text-xs text-danger">{state.error}</p>}
    </form>
  );
}
