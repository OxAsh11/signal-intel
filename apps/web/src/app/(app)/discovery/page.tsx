import type { Metadata } from 'next';
import Link from 'next/link';
import { Card, FamilyTag, HealthIndicator, MetricTile } from '@signal/ui';
import type { HealthStatus } from '@signal/ui';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext } from '@/lib/org';
import { isNotSetUp, radarTypeLabel, yieldLabel } from '@/lib/radars';

export const metadata: Metadata = { title: 'Discovery' };

type Radar = {
  id: string;
  name: string;
  connector_type: string;
  status: string;
  last_fetched_at: string | null;
  last_error: string | null;
  record_count: number;
  prefiltered_count: number;
  yield_count: number;
  brief: { id: string; name: string } | { id: string; name: string }[] | null;
};

const STATUSES = ['pending', 'extracted', 'irrelevant', 'failed'] as const;
const HEALTH: Record<string, HealthStatus> = { active: 'ok', silent: 'silent', error: 'incident', paused: 'degraded' };
const nf = new Intl.NumberFormat('en');

/**
 * Discovery (pool family): from pages fetched to companies matched, then each radar's volume,
 * pre-filtered share and yield. Nothing is dropped silently: every step is counted.
 */
export default async function DiscoveryPage() {
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;
  const ws = ctx.workspace.id;
  const count = (q: PromiseLike<{ count: number | null }>) => Promise.resolve(q).then((r) => r.count ?? 0);

  const [{ data: radarRows }, prefiltered, ...statusCounts] = await Promise.all([
    supabase
      .from('radar')
      .select('id, name, connector_type, status, last_fetched_at, last_error, record_count, prefiltered_count, yield_count, brief:brief_id(id, name)')
      .eq('workspace_id', ws)
      .order('created_at', { ascending: false }),
    count(supabase.from('source_record').select('id', { count: 'exact', head: true }).eq('workspace_id', ws).eq('status_detail', 'prefilter')),
    ...STATUSES.map((s) =>
      count(supabase.from('source_record').select('id', { count: 'exact', head: true }).eq('workspace_id', ws).eq('extraction_status', s)),
    ),
  ]);
  const radars = (radarRows ?? []) as unknown as Radar[];
  const [pending, extracted, irrelevant, failed] = statusCounts as number[];
  const total = pending + extracted + irrelevant + failed;
  const matched = radars.reduce((a, r) => a + (r.yield_count ?? 0), 0);
  const steps: [string, number, string][] = [
    ['Pages fetched', total, 'every record the radars stored'],
    ['Passed the key-term filter', total - prefiltered, `${nf.format(prefiltered)} filtered for free`],
    ['Judged relevant by AI', extracted, 'facts and quotes extracted'],
    ['Led to a match', matched, 'Confirmed or Likely companies'],
  ];
  const pct = (n: number) => (total ? Math.max(n ? 1.5 : 0, (n / total) * 100) : 0);

  return (
    <>
      <div className="flex flex-col gap-2">
        <span className="si-eyebrow">Discovery</span>
        <h1 className="si-h1">
          {total ? `From ${nf.format(total)} pages to ${nf.format(matched)} matches` : 'Where your Briefs are looking'}
        </h1>
        <p className="si-lede">
          Each step is cheaper than the next and keeps only what matters, so the AI reads a small share. Nothing is dropped
          silently: every filtered page is counted. Radars are created from a Brief&apos;s sources.
        </p>
      </div>

      <Card family="pool" kind="feature" aria-labelledby="funnel-h">
        <FamilyTag family="pool">Discovery · funnel</FamilyTag>
        <h2 id="funnel-h" className="si-h2 text-[20px]">What happened to every page</h2>
        <div className="grid grid-cols-[minmax(140px,240px)_minmax(0,1fr)_auto] items-center gap-x-4 gap-y-3">
          {steps.map(([label, n, note]) => (
            <div key={label} className="contents">
              <span className="font-medium text-ink">{label}</span>
              <div className="h-5 rounded-xs bg-surface/70">
                <div className="h-full rounded-xs bg-ink" style={{ width: `${pct(n)}%` }} />
              </div>
              <span className="text-right">
                <span className="font-mono text-ink">{nf.format(n)}</span>{' '}
                <span className="hidden text-[12px] text-ink sm:inline">· {note}</span>
              </span>
            </div>
          ))}
        </div>
      </Card>

      <div className="si-tiles">
        <MetricTile family="pool" label="Waiting to be read" value={nf.format(pending)} href="/discovery" version="def v1" />
        <MetricTile family="pool" label="Not relevant" value={nf.format(irrelevant)} href="/discovery" delta="kept, never deleted" version="def v1" />
        <MetricTile family="lava" label="Failed" value={nf.format(failed)} href="/discovery" delta="retried automatically" version="def v1" />
      </div>

      <Card family="pool" className="si-flush" aria-labelledby="radars-h">
        <div className="flex flex-wrap items-center justify-between gap-3 p-6 pb-4">
          <div className="flex items-center gap-3">
            <FamilyTag family="pool">Radars</FamilyTag>
            <h2 id="radars-h" className="si-h2">{radars.length} radar{radars.length === 1 ? '' : 's'}</h2>
          </div>
          <span className="text-[13px] text-ink-muted">Yield: share of AI-judged pages that led to a Confirmed or Likely company</span>
        </div>
        {radars.length === 0 ? (
          <p className="m-0 px-6 pb-6 text-ink-muted">
            No radars yet. Publish a Brief in <Link href="/briefs">Brief Studio</Link>; its sources become radars.
          </p>
        ) : (
          <div className="si-table-box bg-surface">
            <table className="si-table min-w-[820px]">
              <thead>
                <tr><th>Radar</th><th>Brief</th><th>Health</th><th className="text-right">Pages</th><th className="text-right">Pre-filtered</th><th className="text-right">Yield</th></tr>
              </thead>
              <tbody>
                {radars.map((r) => {
                  const brief = Array.isArray(r.brief) ? (r.brief[0] ?? null) : r.brief;
                  const detail = isNotSetUp(r.last_error)
                    ? 'not set up yet'
                    : r.last_error ?? (r.last_fetched_at ? `fetched ${new Date(r.last_fetched_at).toLocaleDateString()}` : 'not fetched yet');
                  return (
                    <tr key={r.id}>
                      <td>
                        <span className="font-medium text-ink">{r.name}</span>
                        <div className="si-meta">{radarTypeLabel(r.connector_type)}</div>
                      </td>
                      <td>{brief ? <Link href={`/briefs/${brief.id}/radars`} className="text-ink underline">{brief.name}</Link> : '—'}</td>
                      <td><HealthIndicator name={r.status} status={HEALTH[r.status] ?? 'degraded'} detail={detail} /></td>
                      <td className="text-right font-mono">{nf.format(r.record_count)}</td>
                      <td className="text-right font-mono">{nf.format(r.prefiltered_count)}</td>
                      <td className="text-right font-mono">{yieldLabel(r.record_count, r.prefiltered_count, r.yield_count)}</td>
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
