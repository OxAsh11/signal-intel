import { createClient } from '@/lib/supabase/server';

export async function HealthSection({ ws }: { ws: string }) {
  const supabase = await createClient();
  if (!supabase) return null;
  const [{ data: radars }, { count: outbox }] = await Promise.all([
    supabase.from('radar').select('id, name, status, last_run_at, last_error').eq('workspace_id', ws).order('name'),
    supabase.from('outbox').select('id', { count: 'exact', head: true }),
  ]);
  return (
    <section className="si-card">
      <h2 className="si-h2">Health</h2>
      <h3 className="si-h3">Radars</h3>
      <ul className="m-0 pl-5 text-[14px]">
        {(radars ?? []).map((r: { id: string; name: string; status: string | null; last_run_at: string | null; last_error: string | null }) => (
          <li key={r.id}>
            {r.name} — {r.status ?? 'unknown'}
            {r.last_error ? <span className="text-danger"> · {r.last_error}</span> : null}
          </li>
        ))}
        {!(radars ?? []).length ? <li className="text-ink-muted">No radars yet.</li> : null}
      </ul>
      <p className="m-0 text-[13px] text-ink-muted">Outbox backlog: {outbox ?? 0}</p>
      <p className="m-0 text-[13px] text-ink-muted">Worker queue: see worker status endpoint.</p>
    </section>
  );
}