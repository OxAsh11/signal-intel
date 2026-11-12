'use client';

import { useActionState } from 'react';
import { createRadar } from '@/app/actions/radars';

interface Props {
  briefId: string;
  workspaceId: string;
}

const CONNECTOR_OPTIONS = [
  { value: 'csv_upload', label: 'CSV upload' },
  { value: 'website', label: 'Company websites' },
  { value: 'news', label: 'News & publications' },
  { value: 'job_post', label: 'Job postings' },
] as const;

export function RadarAddForm({ briefId, workspaceId }: Props) {
  const [state, formAction, pending] = useActionState(createRadar, {});

  return (
    <form action={formAction} className="flex flex-col gap-3 rounded-lg border border-border p-4">
      <h3 className="m-0 text-sm font-semibold">Add radar</h3>

      <input type="hidden" name="brief" value={briefId} />
      <input type="hidden" name="workspace" value={workspaceId} />

      <div className="flex flex-col gap-1">
        <label htmlFor="radar-type" className="text-xs text-ink-muted">Type</label>
        <select
          id="radar-type"
          name="connector_type"
          className="rounded border border-border bg-surface px-2 py-1.5 text-sm"
          required
        >
          <option value="">Select type…</option>
          {CONNECTOR_OPTIONS.map(opt => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="radar-name" className="text-xs text-ink-muted">Name</label>
        <input
          id="radar-name"
          name="name"
          type="text"
          placeholder="e.g. Competitor websites"
          className="rounded border border-border bg-surface px-2 py-1.5 text-sm"
          required
        />
      </div>

      {state.error && (
        <p className="m-0 text-sm text-red-600">{state.error}</p>
      )}

      <button
        type="submit"
        disabled={pending}
        className="self-start rounded bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
      >
        {pending ? 'Adding…' : 'Add radar'}
      </button>
    </form>
  );
}
