import type { Metadata } from 'next';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext, atLeast } from '@/lib/org';
import { mergeAction, skipAction, createNewAction } from './review-actions';

export const metadata: Metadata = { title: 'Review' };

type ReviewRow = {
  id: string;
  match_method: string | null;
  match_confidence: number | null;
  created_at: string;
  mention: {
    raw_name: string | null;
    raw_domain: string | null;
    raw_country: string | null;
    entity_type: string | null;
  } | null;
  entity: {
    display_name: string | null;
  } | null;
};

export default async function ReviewPage() {
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;

  const canResolve = atLeast(ctx.workspaceRole, 'Editor');

  const { data: items, error } = await supabase
    .from('review_item')
    .select(
      `id, match_method, match_confidence, created_at,
       mention:mention_id ( raw_name, raw_domain, raw_country, entity_type ),
       entity:candidate_entity ( display_name )`
    )
    .eq('workspace_id', ctx.workspace.id)
    .eq('status', 'pending')
    .order('created_at', { ascending: true })
    .limit(50);

  if (error) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="m-0 text-2xl font-semibold">Review</h1>
        <p className="text-sm text-red-600">Failed to load review queue: {error.message}</p>
      </div>
    );
  }

  const rows = (items ?? []) as unknown as ReviewRow[];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="m-0 text-2xl font-semibold">Review</h1>
        <p className="m-0 text-sm text-ink-muted">
          Identity decisions waiting for human review
          {rows.length > 0 && ` · ${rows.length} pending`}
        </p>
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-ink-muted">No items to review.</p>
      ) : (
        <div className="flex flex-col gap-3">
          {rows.map((item) => {
            const mention = item.mention;
            const candidate = item.entity;
            const confidence = item.match_confidence != null ? `${item.match_confidence}%` : '—';
            const method = item.match_method ?? '—';

            const boundMerge = mergeAction.bind(null, item.id);
            const boundSkip = skipAction.bind(null, item.id);
            const boundCreateNew = createNewAction.bind(null, item.id);

            return (
              <div
                key={item.id}
                className="rounded-lg border border-border bg-surface p-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"
              >
                {/* Mention side */}
                <div className="flex flex-col gap-1 min-w-0">
                  <p className="m-0 text-xs font-medium text-ink-muted uppercase tracking-wide">Mention</p>
                  <p className="m-0 font-medium text-sm truncate">{mention?.raw_name ?? '—'}</p>
                  <p className="m-0 text-xs text-ink-muted">
                    {mention?.raw_domain ?? ''}
                    {mention?.raw_domain && mention?.raw_country ? ' · ' : ''}
                    {mention?.raw_country ?? ''}
                    {mention?.entity_type ? ` · ${mention.entity_type}` : ''}
                  </p>
                </div>

                {/* Candidate side */}
                <div className="flex flex-col gap-1 min-w-0 sm:text-right">
                  <p className="m-0 text-xs font-medium text-ink-muted uppercase tracking-wide">Candidate</p>
                  <p className="m-0 font-medium text-sm truncate">{candidate?.display_name ?? 'None'}</p>
                  <p className="m-0 text-xs text-ink-muted">
                    {method} · {confidence}
                  </p>
                </div>

                {/* Actions */}
                {canResolve && (
                  <div className="flex flex-col gap-2 shrink-0">
                    <div className="flex gap-2">
                      <form action={boundMerge}>
                        <button
                          type="submit"
                          className="rounded border border-green-300 bg-green-50 px-3 py-1 text-xs font-medium text-green-800 hover:bg-green-100"
                        >
                          Merge
                        </button>
                      </form>
                      <form action={boundSkip}>
                        <button
                          type="submit"
                          className="rounded border border-border px-3 py-1 text-xs font-medium text-ink-muted hover:bg-surface-raised"
                        >
                          Skip
                        </button>
                      </form>
                    </div>
                    <form action={boundCreateNew} className="flex gap-2">
                      <input
                        type="text"
                        name="new_name"
                        placeholder="New entity name"
                        className="rounded border border-border px-2 py-1 text-xs min-w-0 w-36 bg-paper text-ink placeholder:text-ink-faint focus:outline-none focus:ring-1 focus:ring-blue-500"
                        required
                      />
                      <button
                        type="submit"
                        className="rounded border border-blue-300 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-800 hover:bg-blue-100 shrink-0"
                      >
                        Create New
                      </button>
                    </form>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
