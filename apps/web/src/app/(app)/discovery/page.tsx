import type { Metadata } from 'next';
import Link from 'next/link';
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

/** Radars per Brief with their volume and yield, and what happened to the records they fetched (M2, M3). */
export default async function DiscoveryPage() {
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;
  const ws = ctx.workspace.id;

  const [{ data: radarRows }, ...statusCounts] = await Promise.all([
    supabase
      .from('radar')
      .select('id, name, connector_type, status, last_fetched_at, last_error, record_count, prefiltered_count, yield_count, brief:brief_id(id, name)')
      .eq('workspace_id', ws)
      .order('created_at', { ascending: false }),
    ...STATUSES.map((s) =>
      supabase.from('source_record').select('id', { count: 'exact', head: true }).eq('workspace_id', ws).eq('extraction_status', s),
    ),
  ]);
  const radars = (radarRows ?? []) as unknown as Radar[];

  return (
    <>
      <div className="flex flex-col gap-1">
        <h1 className="m-0 text-[28px] leading-[34px] font-semibold tracking-[-0.015em]">Discovery</h1>
        <p className="m-0 max-w-[65ch] text-ink-muted">
          Where each Brief is looking, how much it found, and what happened to every record. Yield is the share of judged records that led to a Confirmed or Likely account. Radars are added from a Brief.
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="m-0 text-lg font-semibold">Fetched records</h2>
        <div className="flex flex-wrap gap-3">
          {STATUSES.map((s, i) => (
            <div key={s} className="flex min-w-[130px] flex-col gap-1 rounded-md border border-line bg-surface p-4">
              <span className="si-label">{s}</span>
              <span className="text-[28px] leading-[34px] font-semibold">{statusCounts[i]?.count ?? '—'}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="m-0 text-lg font-semibold">Radars</h2>
        {radars.length === 0 ? (
          <p className="m-0 text-ink-muted">
            No radars yet. Open a Brief in <Link href="/briefs">Brief Studio</Link> and add one.
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Radar</th>
                <th>Brief</th>
                <th>Source</th>
                <th>Status</th>
                <th>Last fetched</th>
                <th>Records</th>
                <th>Pre-filtered</th>
                <th>Yield</th>
              </tr>
            </thead>
            <tbody>
              {radars.map((r) => {
                const brief = Array.isArray(r.brief) ? (r.brief[0] ?? null) : r.brief;
                return (
                  <tr key={r.id}>
                    <td>{r.name}</td>
                    <td>{brief ? <Link href={`/briefs/${brief.id}/radars`}>{brief.name}</Link> : '—'}</td>
                    <td>{radarTypeLabel(r.connector_type)}</td>
                    <td>
                      <span data-radar-status={r.status}>{r.status}</span>
                      {isNotSetUp(r.last_error) ? ': not set up yet' : r.last_error ? `: ${r.last_error}` : ''}
                    </td>
                    <td>{r.last_fetched_at ? new Date(r.last_fetched_at).toLocaleString() : 'Never'}</td>
                    <td>{r.record_count}</td>
                    <td>{r.prefiltered_count}</td>
                    <td>{yieldLabel(r.record_count, r.prefiltered_count, r.yield_count)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
