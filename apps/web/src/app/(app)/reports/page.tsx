import type { Metadata } from 'next';
import { getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';

export const metadata: Metadata = { title: 'Reports' };

export default async function ReportsPage() {
  const ctx = await getOrgContext();
  const supabase = await createClient();
  if (!ctx?.workspace || !supabase) return null;
  const ws = ctx.workspace.id;
  const { data } = await supabase.rpc('report_funnel', { p_ws: ws, p_brief: null });
  const r = (data ?? {}) as { found?: number; matched?: number; confirmed?: number };
  return (
    <section className="si-card">
      <h1 className="si-h1">Reports</h1>
      <p className="si-lede">Funnel across all Briefs in this workspace.</p>
      <ul className="m-0 flex flex-col gap-1 pl-5 text-[14px]">
        <li>Found: {r.found ?? 0}</li>
        <li>Matched: {r.matched ?? 0}</li>
        <li>Confirmed: {r.confirmed ?? 0}</li>
      </ul>
    </section>
  );
}