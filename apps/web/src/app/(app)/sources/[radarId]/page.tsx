import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Card, FamilyTag, HealthIndicator } from '@signal/ui';
import type { HealthStatus } from '@signal/ui';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext } from '@/lib/org';
import { isNotSetUp, radarTypeLabel } from '@/lib/radars';

export const metadata: Metadata = { title: 'Radar records' };

const PAGE_SIZE = 50;

const HEALTH: Record<string, HealthStatus> = {
  active: 'ok',
  silent: 'silent',
  error: 'incident',
  paused: 'degraded',
};

const STATUS_CLASS: Record<string, string> = {
  extracted: 'bg-[var(--positive-soft)] text-[var(--positive)]',
  pending:   'bg-[var(--signal-soft)] text-[var(--signal)]',
  failed:    'bg-[var(--danger-soft)] text-[var(--danger)]',
  irrelevant: 'bg-[var(--surface-sunken)] text-[var(--ink-muted)]',
};

const STATUS_LABEL: Record<string, string> = {
  extracted: 'Relevant',
  pending:   'Waiting',
  failed:    'Failed',
  irrelevant: 'Filtered',
};

function statusLabel(status: string, detail: string | null): string {
  if (status === 'irrelevant' && detail === 'prefilter') return 'Pre-filtered';
  if (status === 'irrelevant' && detail === 'derived') return 'Derived';
  return STATUS_LABEL[status] ?? status;
}

function truncateUrl(url: string | null): string {
  if (!url) return '—';
  try {
    const u = new URL(url);
    const path = u.pathname.replace(/\/$/, '');
    const display = u.hostname.replace(/^www\./, '') + (path.length > 40 ? path.slice(0, 40) + '…' : path);
    return display;
  } catch {
    return url.length > 60 ? url.slice(0, 60) + '…' : url;
  }
}

function ago(iso: string | null): string {
  if (!iso) return '—';
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 864e5);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return months < 12 ? `${months}mo ago` : `${Math.floor(months / 12)}y ago`;
}

export default async function RadarDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ radarId: string }>;
  searchParams: Promise<{ page?: string }>;
}) {
  const [{ radarId }, sp] = await Promise.all([params, searchParams]);
  const page = Math.max(0, parseInt(sp.page ?? '0', 10) || 0);

  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;
  const ws = ctx.workspace.id;

  const { data: radar } = await supabase
    .from('radar')
    .select('id, name, connector_type, status, last_fetched_at, last_error, record_count, brief:brief_id(id, name)')
    .eq('id', radarId)
    .eq('workspace_id', ws)
    .maybeSingle();

  if (!radar) notFound();

  const brief = Array.isArray(radar.brief) ? (radar.brief[0] ?? null) : radar.brief;

  const { data: records, count } = await supabase
    .from('source_record')
    .select('id, url, extraction_status, status_detail, fetched_at, source_type', { count: 'exact' })
    .eq('radar_id', radarId)
    .eq('workspace_id', ws)
    .order('fetched_at', { ascending: false })
    .range(page * PAGE_SIZE, (page + 1) * PAGE_SIZE - 1);

  const total = count ?? 0;
  const pageCount = Math.ceil(total / PAGE_SIZE);

  const detail = isNotSetUp(radar.last_error)
    ? 'not set up yet'
    : radar.last_error ?? (radar.last_fetched_at ? `fetched ${new Date(radar.last_fetched_at).toLocaleDateString()}` : 'not fetched yet');

  return (
    <>
      <div className="flex flex-col gap-2">
        <Link href="/sources" className="text-[13px] text-ink-muted">‹ Sources</Link>
        <h1 className="si-h1">{radar.name}</h1>
        <div className="flex flex-wrap items-center gap-3">
          <span className="si-meta">{radarTypeLabel(radar.connector_type)}</span>
          <HealthIndicator name={radar.status} status={HEALTH[radar.status] ?? 'degraded'} detail={detail} />
          {brief ? (
            <Link href={`/briefs/${brief.id}/radars`} className="si-meta underline">
              {brief.name}
            </Link>
          ) : null}
        </div>
      </div>

      <Card family="pool" className="si-flush" aria-labelledby="records-h">
        <div className="flex flex-wrap items-center justify-between gap-3 p-6 pb-4">
          <div className="flex items-center gap-3">
            <FamilyTag family="pool">Records</FamilyTag>
            <h2 id="records-h" className="si-h2">
              {total.toLocaleString()} record{total === 1 ? '' : 's'}
            </h2>
          </div>
          {pageCount > 1 && (
            <span className="si-meta">
              Page {page + 1} of {pageCount}
            </span>
          )}
        </div>

        {total === 0 ? (
          <p className="m-0 px-6 pb-6 text-ink-muted">No records yet. This radar will populate when it next runs.</p>
        ) : (
          <div className="si-table-box bg-surface">
            <table className="si-table min-w-[700px]">
              <thead>
                <tr>
                  <th style={{ width: '40%' }}>URL</th>
                  <th>Status</th>
                  <th>Detail</th>
                  <th>Fetched</th>
                </tr>
              </thead>
              <tbody>
                {(records ?? []).map((r) => (
                  <tr key={r.id}>
                    <td>
                      {r.url ? (
                        <a
                          href={r.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-medium text-signal no-underline hover:underline"
                          title={r.url}
                        >
                          {truncateUrl(r.url)}
                        </a>
                      ) : (
                        <span className="text-ink-muted">—</span>
                      )}
                      {r.source_type ? (
                        <div className="si-meta mt-0.5">{r.source_type.replace(/_/g, ' ')}</div>
                      ) : null}
                    </td>
                    <td>
                      <span
                        className={`si-chip ${STATUS_CLASS[r.extraction_status] ?? 'bg-[var(--surface-sunken)] text-[var(--ink-muted)]'}`}
                      >
                        {statusLabel(r.extraction_status, r.status_detail)}
                      </span>
                    </td>
                    <td className="text-[13px] text-ink-muted">
                      {r.status_detail && r.status_detail !== 'prefilter' && r.status_detail !== 'derived'
                        ? r.status_detail
                        : '—'}
                    </td>
                    <td className="si-meta whitespace-nowrap" title={r.fetched_at ?? undefined}>
                      {ago(r.fetched_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pageCount > 1 && (
          <div className="flex items-center justify-between gap-4 border-t border-line px-6 py-3">
            {page > 0 ? (
              <Link
                href={`/sources/${radarId}?page=${page - 1}`}
                className="si-btn si-btn-sm"
              >
                ← Prev
              </Link>
            ) : (
              <span />
            )}
            <span className="si-meta">
              {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total.toLocaleString()}
            </span>
            {page < pageCount - 1 ? (
              <Link
                href={`/sources/${radarId}?page=${page + 1}`}
                className="si-btn si-btn-sm"
              >
                Next →
              </Link>
            ) : (
              <span />
            )}
          </div>
        )}
      </Card>
    </>
  );
}
