import { notFound } from 'next/navigation';
import Link from 'next/link';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext, atLeast } from '@/lib/org';
import { removeRadar } from '@/app/actions/radars';
import { RadarAddForm } from './radar-add-form';

export const metadata = { title: 'Radars' };

type RadarStatus = 'active' | 'error' | 'silent' | string;

function statusBadge(status: RadarStatus) {
  const base = 'inline-block rounded-full px-2 py-0.5 text-xs font-medium';
  if (status === 'active') return `${base} bg-green-100 text-green-800`;
  if (status === 'error') return `${base} bg-red-100 text-red-700`;
  if (status === 'silent') return `${base} bg-amber-100 text-amber-700`;
  return `${base} bg-gray-100 text-gray-600`;
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
    .select('id, connector_type, name, status, record_count, yield_count, last_fetched_at, last_error, radar_query(count)')
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
            const queryCount = Array.isArray(r.radar_query)
              ? r.radar_query[0]?.count ?? 0
              : 0;

            return (
              <div key={r.id} className="flex flex-col gap-2 rounded-lg border border-border p-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm">{r.name}</span>
                    <span className={statusBadge(r.status ?? 'unknown')}>{r.status ?? 'unknown'}</span>
                  </div>
                  <p className="m-0 text-xs text-ink-muted capitalize">{r.connector_type?.replace(/_/g, ' ')}</p>
                  <p className="m-0 text-xs text-ink-muted">
                    Records: {r.record_count ?? 0} · Yields: {r.yield_count ?? 0} · Queries: {queryCount} · Last fetched: {formatDate(r.last_fetched_at)}
                  </p>
                  {r.last_error && (
                    <p className="m-0 text-xs text-red-600 truncate max-w-sm" title={r.last_error}>
                      Error: {r.last_error}
                    </p>
                  )}
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
                        className="rounded border border-border px-2 py-1 text-xs text-red-600 hover:bg-red-50"
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
