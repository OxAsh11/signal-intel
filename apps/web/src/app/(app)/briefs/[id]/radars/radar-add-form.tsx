'use client';

import { useActionState, useState } from 'react';
import { createRadar } from '@/app/actions/radars';
import { RADAR_TYPES, WEB_SEARCH_TYPES } from '@/lib/radars';

interface Props {
  briefId: string;
  workspaceId: string;
}

export function RadarAddForm({ briefId, workspaceId }: Props) {
  const [state, formAction, pending] = useActionState(createRadar, {});
  const [type, setType] = useState('');

  return (
    <form action={formAction} className="si-card">
      <h3 className="m-0 text-sm font-semibold">Add radar</h3>

      <input type="hidden" name="brief" value={briefId} />
      <input type="hidden" name="workspace" value={workspaceId} />

      <div className="flex flex-col gap-1">
        <label htmlFor="radar-type" className="text-xs text-ink-muted">Type</label>
        <select
          id="radar-type"
          name="connector_type"
          className="rounded-sm border border-line-strong bg-surface px-3 py-2 text-sm text-ink"
          required
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          <option value="">Select type…</option>
          {RADAR_TYPES.map((opt) => (
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
          placeholder="e.g. Hotel tenders in Germany"
          className="rounded-sm border border-line-strong bg-surface px-3 py-2 text-sm text-ink"
          required
        />
      </div>

      {WEB_SEARCH_TYPES.has(type) ? (
        <div className="flex flex-col gap-1">
          <label htmlFor="radar-sites" className="text-xs text-ink-muted">
            Limit to sites (optional, one domain per line)
          </label>
          <textarea
            id="radar-sites"
            name="sites"
            rows={3}
            placeholder={type === 'social_post' ? 'Leave empty for the main public social sites' : 'e.g. ted.europa.eu'}
            className="rounded-sm border border-line-strong bg-surface px-3 py-2 text-sm text-ink"
          />
          <p className="m-0 text-xs text-ink-muted">
            Found through web search. Pages that block crawlers keep only the search snippet.
          </p>
        </div>
      ) : null}

      {state.error && (
        <p className="m-0 text-sm text-danger">{state.error}</p>
      )}

      <button
        type="submit"
        disabled={pending}
        className="si-btn si-btn-primary self-start"
      >
        {pending ? 'Adding…' : 'Add radar'}
      </button>
    </form>
  );
}
