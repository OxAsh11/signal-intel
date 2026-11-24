import type { Metadata } from 'next';
import { getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';

export const metadata: Metadata = { title: 'Reports' };

async function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="si-card">
      <h2 className="si-h2">{title}</h2>
      {children}
    </section>
  );
}

export default async function ReportsPage() {
  const ctx = await getOrgContext();
  const supabase = await createClient();
  if (!ctx?.workspace || !supabase) return null;
  const ws = ctx.workspace.id;

  const [funnel, coverage, cost, sources, calibration] = await Promise.all([
    supabase.rpc('report_funnel', { p_ws: ws, p_brief: null }),
    supabase.rpc('report_coverage', { p_ws: ws, p_brief: null }),
    supabase.rpc('report_cost', { p_ws: ws, p_brief: null, p_months: 3 }),
    supabase.rpc('report_sources', { p_ws: ws, p_brief: null }),
    supabase.rpc('report_calibration', { p_ws: ws, p_brief: null }),
  ]);

  const f = (funnel.data ?? {}) as { found?: number; matched?: number; confirmed?: number };
  const cov = (coverage.data ?? {}) as { columns?: { key: string; label: string; filled: number; total: number }[] };
  const c = (cost.data ?? {}) as { total?: number; by_provider?: { provider: string; cost: number }[] };
  const s = (sources.data ?? {}) as { radars?: { radar_id: string; name: string }[] };
  const cal = (calibration.data ?? {}) as { marked_wrong?: number };

  return (
    <div className="flex flex-col gap-4">
      <h1 className="si-h1">Reports</h1>
      <Card title="Funnel">
        <ul className="m-0 pl-5 text-[13px]">
          <li>Found: {f.found ?? 0}</li>
          <li>Matched: {f.matched ?? 0}</li>
          <li>Confirmed: {f.confirmed ?? 0}</li>
        </ul>
      </Card>
      <Card title="Coverage">
        <ul className="m-0 pl-5 text-[13px]">
          {(cov.columns ?? []).map((col) => (
            <li key={col.key}>
              {col.label}: {col.filled} / {col.total}
            </li>
          ))}
          {!(cov.columns ?? []).length ? <li>Fills in after your first accounts are found.</li> : null}
        </ul>
      </Card>
      <Card title="Cost">
        <p className="m-0 text-[13px]">Total: ${Number(c.total ?? 0).toFixed(2)}</p>
        <ul className="m-0 pl-5 text-[13px]">
          {(c.by_provider ?? []).map((p) => (
            <li key={p.provider}>
              {p.provider}: ${Number(p.cost).toFixed(2)}
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Sources">
        <ul className="m-0 pl-5 text-[13px]">
          {(s.radars ?? []).map((r) => (
            <li key={r.radar_id}>{r.name}</li>
          ))}
          {!(s.radars ?? []).length ? <li>Fills in after your first radar runs.</li> : null}
        </ul>
      </Card>
      <Card title="Calibration">
        <p className="m-0 text-[13px]">Marked wrong: {cal.marked_wrong ?? 0}</p>
      </Card>
    </div>
  );
}