import { notFound } from 'next/navigation';
import Link from 'next/link';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext, atLeast } from '@/lib/org';
import { removeRadar } from '@/app/actions/radars';
import { RadarAddForm } from './radar-add-form';
import { RadarQueries, type RadarQueryRow } from './radar-queries';
import { isNotSetUp, radarTypeLabel, yieldLabel } from '@/lib/radars';

export const metadata = { title: 'Radars' };

type RadarStatus = 'active' | 'error' | 'silent' | string;

function statusBadge(status: RadarStatus) {
  if (status === 'active') return 'si-chip si-reply-interested';
  if (status === 'error') return 'si-chip si-reply-unsubscribe';
  if (status === 'silent') return 'si-chip si-reply-notnow';
  return 'si-chip si-reply-notinterested';
}

function formatDate(iso: string | null) {
  if (!iso) return 'never';
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' });
}

export default async function RadarsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;

  const { data: radars, error } = await supabase
    .from('radar')
    .select(
      'id, connector_type, name, status, record_count, prefiltered_count, yield_count, lexicon, lexicon_edited, generated_lexicon, last_fetched_at, last_error, radar_query(id, query_text, source, record_count, prefiltered_count, yield_count)',
    )
    .eq('brief_id', id)
    .neq('status', 'paused')
    .order('created_at', { ascending: true });

  if (error) notFound();

  const canEdit = atLeast(ctx.workspaceRole, 'Editor');

  async function handleRemove(form: FormData): Promise<void> {
    'use server';
    await removeRadar({}, form);
  }

  return (
    <>
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="m-0 text-2xl font-semibold">Radars</h1>
          <p className="m-0 text-sm text-ink-muted">Signal sources attached to this brief</p>
        </div>
        <Link href={`/briefs/${id}`} className="text-sm text-ink-muted hover:text-ink">
          ← Back to brief
        </Link>
      </div>

      {radars && radars.length > 0 ? (
        <div className="mt-4 flex flex-col gap-2">
          {radars.map(r => {
            const queries = (Array.isArray(r.radar_query) ? r.radar_query : []) as RadarQueryRow[];
            const notSetUp = isNotSetUp(r.last_error);

            return (
              <div key={r.id} className="si-card sm:flex-row sm:items-start sm:justify-between">
                <div className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm">{r.name}</span>
                    <span className={statusBadge(r.status ?? 'unknown')}>{r.status ?? 'unknown'}</span>
                  </div>
                  <p className="m-0 text-xs text-ink-muted">{radarTypeLabel(r.connector_type ?? '')}</p>
                  <p className="m-0 text-xs text-ink-muted">
                    Records: {r.record_count ?? 0} · Pre-filtered: {r.prefiltered_count ?? 0} · Yield:{' '}
                    {yieldLabel(r.record_count ?? 0, r.prefiltered_count ?? 0, r.yield_count ?? 0)} · Last fetched:{' '}
                    {formatDate(r.last_fetched_at)}
                  </p>
                  {notSetUp ? (
                    <p className="m-0 text-xs text-ink-muted">
                      Not set up yet: web-search radars start once the Brave Search key is added to the worker.
                    </p>
                  ) : r.last_error ? (
                    <p className="m-0 text-xs text-danger truncate max-w-sm" title={r.last_error}>
                      Error: {r.last_error}
                    </p>
                  ) : null}
                  {r.connector_type !== 'csv_upload' ? (
                    <RadarQueries
                      briefId={id}
                      radarId={r.id}
                      queries={queries}
                      lexicon={(r.lexicon as string[] | null) ?? []}
                      lexiconEdited={Boolean(r.lexicon_edited)}
                      generatedLexicon={(r.generated_lexicon as string[] | null) ?? []}
                      canEdit={canEdit}
                    />
                  ) : null}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {r.connector_type === 'csv_upload' && (
                    <Link
                      href={`/briefs/${id}/radars/upload?radar_id=${r.id}`}
                      className="text-xs text-ink-muted hover:text-ink"
                    >
                      Upload CSV →
                    </Link>
                  )}
                  {canEdit && (
                    <form action={handleRemove}>
                      <input type="hidden" name="id" value={r.id} />
                      <button
                        type="submit"
                        className="si-btn si-btn-sm text-danger"
                      >
                        Remove
                      </button>
                    </form>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="mt-4 text-sm text-ink-muted">No radars yet. Add one below.</p>
      )}

      {canEdit && (
        <div className="mt-6">
          <RadarAddForm briefId={id} workspaceId={ctx.workspace.id} />
        </div>
      )}
    </>
  );
}
