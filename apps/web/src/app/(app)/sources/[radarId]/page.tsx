import { notFound } from 'next/navigation';
import Link from 'next/link';
import { Card, HealthIndicator } from '@signal/ui';
import type { HealthStatus } from '@signal/ui';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext, atLeast } from '@/lib/org';
import { triggerRadarFetch } from '@/app/actions/radars';
import { RadarQueries, type RadarQueryRow } from '@/app/(app)/briefs/[id]/radars/radar-queries';
import { isNotSetUp, radarTypeLabel, yieldLabel } from '@/lib/radars';

const HEALTH: Record<string, HealthStatus> = {
  active: 'ok',
  silent: 'silent',
  error: 'incident',
  paused: 'degraded',
};

const nf = new Intl.NumberFormat('en');

function fmt(iso: string | null) {
  if (!iso) return 'never';
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' });
}

function fmtTime(iso: string | null) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export default async function RadarDetailPage({ params }: { params: Promise<{ radarId: string }> }) {
  const { radarId } = await params;
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;
  const ws = ctx.workspace.id;

  // Fetch radar with brief + queries in one round-trip.
  const { data: radar, error } = await supabase
    .from('radar')
    .select(
      'id, name, connector_type, status, last_fetched_at, last_error, record_count, prefiltered_count, yield_count, lexicon, lexicon_edited, generated_lexicon, brief:brief_id(id, name), radar_query(id, query_text, source, record_count, prefiltered_count, yield_count)',
    )
    .eq('id', radarId)
    .eq('workspace_id', ws)
    .single();

  if (error || !radar) notFound();

  // Counts by extraction_status for this radar.
  const count = (q: PromiseLike<{ count: number | null }>) => Promise.resolve(q).then((r) => r.count ?? 0);
  const [prefiltered, pending, extracted, irrelevant, failed, recentRows] = await Promise.all([
    count(supabase.from('source_record').select('id', { count: 'exact', head: true }).eq('radar_id', radarId).eq('status_detail', 'prefilter')),
    count(supabase.from('source_record').select('id', { count: 'exact', head: true }).eq('radar_id', radarId).eq('extraction_status', 'pending')),
    count(supabase.from('source_record').select('id', { count: 'exact', head: true }).eq('radar_id', radarId).eq('extraction_status', 'extracted')),
    count(supabase.from('source_record').select('id', { count: 'exact', head: true }).eq('radar_id', radarId).eq('extraction_status', 'irrelevant')),
    count(supabase.from('source_record').select('id', { count: 'exact', head: true }).eq('radar_id', radarId).eq('extraction_status', 'failed')),
    supabase
      .from('source_record')
      .select('id, url, extraction_status, status_detail, fetched_at')
      .eq('radar_id', radarId)
      .order('fetched_at', { ascending: false })
      .limit(20)
      .then((r) => r.data ?? []),
  ]);

  const total = pending + extracted + irrelevant + failed;
  const yielded = radar.yield_count ?? 0;
  const steps: [string, number, string][] = [
    ['Pages fetched', total, 'every URL stored'],
    ['Passed key-term filter', total - prefiltered, `${nf.format(prefiltered)} filtered for free`],
    ['Judged relevant', extracted, 'facts extracted by AI'],
    ['Led to a match', yielded, 'Confirmed or Likely'],
  ];
  const pct = (n: number) => (total ? Math.max(n ? 1.5 : 0, (n / total) * 100) : 0);

  const brief = Array.isArray(radar.brief) ? (radar.brief[0] ?? null) : radar.brief;
  const queries = (Array.isArray(radar.radar_query) ? radar.radar_query : []) as RadarQueryRow[];
  const notSetUp = isNotSetUp(radar.last_error);
  const canEdit = atLeast(ctx.workspaceRole, 'Editor');

  async function handleFetch(form: FormData): Promise<void> {
    'use server';
    await triggerRadarFetch({}, form);
  }

  const statusDetail = notSetUp
    ? 'not set up yet'
    : radar.last_error ?? (radar.last_fetched_at ? `fetched ${fmt(radar.last_fetched_at)}` : 'not fetched yet');

  return (
    <>
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-3">
            <h1 className="si-h1 m-0">{radar.name}</h1>
            <HealthIndicator
              name={radar.status ?? 'unknown'}
              status={HEALTH[radar.status ?? ''] ?? 'degraded'}
              detail={statusDetail}
            />
          </div>
          <p className="m-0 text-[13px] text-ink-muted">
            {radarTypeLabel(radar.connector_type ?? '')}
            {brief ? (
              <>
                {' · '}
                <Link href={`/briefs/${brief.id}/radars`} className="underline hover:text-ink">
                  {brief.name}
                </Link>
              </>
            ) : null}
            {' · '}Last fetched: {fmt(radar.last_fetched_at)}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {canEdit && radar.connector_type !== 'csv_upload' && (
            <form action={handleFetch}>
              <input type="hidden" name="id" value={radar.id} />
              <button type="submit" className="si-btn si-btn-sm">
                Re-fetch now
              </button>
            </form>
          )}
          <Link href="/sources" className="text-[13px] text-ink-muted hover:text-ink">
            ← Sources
          </Link>
        </div>
      </div>

      {/* Not-set-up banner */}
      {notSetUp && (
        <div className="rounded-[6px] border border-marker bg-marker-soft px-4 py-3 text-[13px] text-caution">
          <strong>Brave Search not configured.</strong> This radar will start fetching once{' '}
          <code>BRAVE_SEARCH_API_KEY</code> and <code>SEARCH_PROVIDER=brave</code> are added to the
          worker environment. The worker checks the outbox every 5 seconds, so results appear within
          minutes of the keys being set.
        </div>
      )}

      {/* Error banner (non-setup errors) */}
      {!notSetUp && radar.last_error && (
        <div className="rounded-[6px] border border-danger bg-danger-soft px-4 py-3 text-[13px] text-danger">
          <strong>Last error:</strong> {radar.last_error}
        </div>
      )}

      {/* Pipeline funnel */}
      <Card aria-labelledby="funnel-h">
        <span className="rounded-[4px] bg-surface-sunken border border-line px-2 py-0.5 text-[11px] font-medium text-ink-muted self-start">Radar · funnel</span>
        <h2 id="funnel-h" className="si-h2">
          {total ? `${nf.format(total)} pages fetched` : 'No pages fetched yet'}
        </h2>
        <div className="grid grid-cols-[minmax(140px,220px)_minmax(0,1fr)_auto] items-center gap-x-4 gap-y-3">
          {steps.map(([label, n, note]) => (
            <div key={label} className="contents">
              <span className="font-medium text-ink">{label}</span>
              <div className="h-5 rounded-xs bg-surface/70">
                <div className="h-full rounded-xs bg-ink" style={{ width: `${pct(n)}%` }} />
              </div>
              <span className="text-right">
                <span className="font-mono text-ink">{nf.format(n)}</span>{' '}
                <span className="hidden text-[12px] text-ink-muted sm:inline">· {note}</span>
              </span>
            </div>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap gap-4 text-[13px] text-ink-muted">
          <span>Pending: <strong className="text-ink">{nf.format(pending)}</strong></span>
          <span>Irrelevant: <strong className="text-ink">{nf.format(irrelevant)}</strong></span>
          {failed > 0 && <span>Failed: <strong className="text-danger">{nf.format(failed)}</strong></span>}
          <span>Yield: <strong className="text-ink">{yieldLabel(total, prefiltered, yielded)}</strong></span>
        </div>
      </Card>

      {/* Queries + lexicon */}
      {radar.connector_type !== 'csv_upload' && (
        <Card aria-labelledby="queries-h">
          <h2 id="queries-h" className="si-h2">Queries &amp; pre-filter</h2>
          <RadarQueries
            briefId={brief?.id ?? ''}
            radarId={radar.id}
            queries={queries}
            lexicon={(radar.lexicon as string[] | null) ?? []}
            lexiconEdited={Boolean(radar.lexicon_edited)}
            generatedLexicon={(radar.generated_lexicon as string[] | null) ?? []}
            canEdit={canEdit}
          />
        </Card>
      )}

      {/* Recent pages */}
      <Card className="si-flush" aria-labelledby="pages-h">
        <div className="flex flex-wrap items-center justify-between gap-3 p-4 pb-3">
          <h2 id="pages-h" className="si-h2">20 most recent pages</h2>
        </div>
        {recentRows.length === 0 ? (
          <p className="m-0 px-4 pb-4 text-[13px] text-ink-muted">No pages fetched yet.</p>
        ) : (
          <div className="si-table-box bg-surface">
            <table className="si-table min-w-[640px]">
              <thead>
                <tr>
                  <th>URL</th>
                  <th>Status</th>
                  <th className="text-right">Fetched</th>
                </tr>
              </thead>
              <tbody>
                {recentRows.map((row) => {
                  const isPrefiltered = row.status_detail === 'prefilter';
                  const label = isPrefiltered
                    ? 'pre-filtered'
                    : (row.extraction_status as string) ?? '—';
                  const cls =
                    label === 'extracted'
                      ? 'text-ok'
                      : label === 'failed'
                        ? 'text-danger'
                        : 'text-ink-muted';
                  return (
                    <tr key={row.id}>
                      <td>
                        <a
                          href={row.url}
                          target="_blank"
                          rel="noreferrer"
                          className="max-w-[400px] truncate block font-mono text-xs text-ink hover:underline"
                          title={row.url}
                        >
                          {row.url}
                        </a>
                      </td>
                      <td>
                        <span className={`text-xs ${cls}`}>{label}</span>
                      </td>
                      <td className="text-right text-xs text-ink-muted whitespace-nowrap">
                        {fmtTime(row.fetched_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
