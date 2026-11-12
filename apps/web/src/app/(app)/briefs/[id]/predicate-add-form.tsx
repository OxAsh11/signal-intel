'use client';
import { useActionState } from 'react';
import { createWorkspacePredicate } from '@/app/actions/briefs';

export function PredicateAddForm({ workspaceId }: { workspaceId: string }) {
  const [state, formAction, isPending] = useActionState(createWorkspacePredicate, {});
  return (
    <form action={formAction} className="flex flex-col gap-2">
      <input type="hidden" name="workspace" value={workspaceId} />
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Key</span>
          <input name="key" required placeholder="e.g. employees"
            pattern="[a-z][a-z0-9_.]{1,79}"
            title="Lowercase letters, digits, underscores, dots; starts with a letter"
            className="rounded border border-line bg-surface px-2 py-1 font-mono text-sm" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Label</span>
          <input name="label" required placeholder="e.g. Employee count"
            className="rounded border border-line bg-surface px-2 py-1 text-sm" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Type</span>
          <select name="value_type" required
            className="rounded border border-line bg-surface px-2 py-1 text-sm">
            <option value="text">text</option>
            <option value="number">number</option>
            <option value="boolean">boolean</option>
            <option value="enum">enum</option>
            <option value="date">date</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Allowed values (enum, JSON)</span>
          <input name="allowed_values" placeholder='["a","b"]'
            className="rounded border border-line bg-surface px-2 py-1 font-mono text-sm" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Extraction hint</span>
          <input name="extraction_hint" placeholder="How to detect this…"
            className="rounded border border-line bg-surface px-2 py-1 text-sm" />
        </label>
        <button type="submit" disabled={isPending}
          className="rounded bg-accent px-3 py-1 text-sm font-medium text-white hover:bg-accent/90 disabled:opacity-60">
          Add column
        </button>
      </div>
      {state?.error && <p className="text-xs text-danger">{state.error}</p>}
    </form>
  );
}
