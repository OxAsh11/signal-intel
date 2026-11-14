'use client';

import { useActionState } from 'react';
import {
  addRadarQuery,
  removeRadarQuery,
  resetRadarLexicon,
  updateRadarLexicon,
} from '@/app/actions/radars';
import { yieldLabel } from '@/lib/radars';

export type RadarQueryRow = {
  id: string;
  query_text: string;
  source: string;
  record_count: number;
  prefiltered_count: number;
  yield_count: number;
};

type Props = {
  briefId: string;
  radarId: string;
  queries: RadarQueryRow[];
  lexicon: string[];
  /** True once a person has edited the terms; generation then leaves them alone. */
  lexiconEdited: boolean;
  generatedLexicon: string[];
  canEdit: boolean;
};

function RemoveQuery({ briefId, queryId }: { briefId: string; queryId: string }) {
  const [state, action, pending] = useActionState(removeRadarQuery, {});
  return (
    <form action={action} className="inline">
      <input type="hidden" name="brief" value={briefId} />
      <input type="hidden" name="query" value={queryId} />
      <button type="submit" disabled={pending} className="text-xs text-ink-muted hover:text-ink">
        Remove
      </button>
      {state.error ? <span className="ml-2 text-xs text-red-600">{state.error}</span> : null}
    </form>
  );
}

/** A radar's queries with volume and yield per query (M2), plus the editable pre-filter lexicon. */
export function RadarQueries({
  briefId,
  radarId,
  queries,
  lexicon,
  lexiconEdited,
  generatedLexicon,
  canEdit,
}: Props) {
  const [addState, addAction, adding] = useActionState(addRadarQuery, {});
  const [lexState, lexAction, saving] = useActionState(updateRadarLexicon, {});
  const [resetState, resetAction, resetting] = useActionState(resetRadarLexicon, {});

  return (
    <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-ink-muted">
        Queries ({queries.length}) and pre-filter terms ({lexicon.length})
      </summary>
      <div className="mt-2 flex flex-col gap-3">
        {queries.length ? (
          <table className="text-xs">
            <thead>
              <tr>
                <th className="text-left">Query</th>
                <th className="text-left">Records</th>
                <th className="text-left">Pre-filtered</th>
                <th className="text-left">Yield</th>
                {canEdit ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {queries.map((q) => (
                <tr key={q.id}>
                  <td>
                    {q.query_text}
                    {q.source === 'user' ? <span className="ml-1 text-ink-muted">(yours)</span> : null}
                  </td>
                  <td>{q.record_count}</td>
                  <td>{q.prefiltered_count}</td>
                  <td>{yieldLabel(q.record_count, q.prefiltered_count, q.yield_count)}</td>
                  {canEdit ? (
                    <td>
                      <RemoveQuery briefId={briefId} queryId={q.id} />
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="m-0 text-ink-muted">No queries yet. They are written when the Brief is published.</p>
        )}

        {canEdit ? (
          <form action={addAction} className="flex flex-wrap items-center gap-2">
            <input type="hidden" name="brief" value={briefId} />
            <input type="hidden" name="radar" value={radarId} />
            <input
              name="text"
              type="text"
              maxLength={500}
              placeholder="Add a search query"
              className="min-w-[240px] rounded border border-border bg-surface px-2 py-1"
            />
            <button type="submit" disabled={adding} className="rounded border border-border px-2 py-1">
              Add query
            </button>
            {addState.error ? <span className="text-red-600">{addState.error}</span> : null}
          </form>
        ) : null}

        <div className="flex flex-col gap-1">
          <span className="text-ink-muted">
            Pre-filter terms: a record needs at least one of these before the AI reads it. Records
            without any are counted as pre-filtered. Leave empty to let everything through.
          </span>
          {canEdit ? (
            <form action={lexAction} className="flex flex-col gap-1">
              <input type="hidden" name="brief" value={briefId} />
              <input type="hidden" name="radar" value={radarId} />
              <textarea
                name="terms"
                rows={4}
                defaultValue={lexicon.join('\n')}
                placeholder="One term or phrase per line"
                className="rounded border border-border bg-surface px-2 py-1"
              />
              <button type="submit" disabled={saving} className="self-start rounded border border-border px-2 py-1">
                Save terms
              </button>
              {lexState.error ? <span className="text-red-600">{lexState.error}</span> : null}
              {lexState.ok ? <span className="text-ink-muted">Saved. New queries will not replace your terms.</span> : null}
            </form>
          ) : (
            <span>{lexicon.join(', ') || 'None'}</span>
          )}
          {canEdit && lexiconEdited ? (
            <form action={resetAction} className="flex flex-wrap items-center gap-2">
              <input type="hidden" name="brief" value={briefId} />
              <input type="hidden" name="radar" value={radarId} />
              <button type="submit" disabled={resetting} className="rounded border border-border px-2 py-1">
                Restore suggested terms
              </button>
              <span className="text-ink-muted">
                {generatedLexicon.length
                  ? `${generatedLexicon.length} suggested: ${generatedLexicon.slice(0, 5).join(', ')}${generatedLexicon.length > 5 ? '…' : ''}`
                  : 'No suggestions yet; they arrive with the next query generation.'}
              </span>
              {resetState.error ? <span className="text-red-600">{resetState.error}</span> : null}
            </form>
          ) : null}
        </div>
      </div>
    </details>
  );
}
