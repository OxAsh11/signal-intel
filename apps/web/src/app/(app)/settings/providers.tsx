import { moveProvider } from '@/app/actions/providers';
import { atLeast, getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';

type DbRow = {
  key: string;
  kind: string;
  price_usd: number | string;
  month_spend_usd: number | string;
  attempts: number;
  last_error: string | null;
  last_error_at: string | null;
};
type WorkerRow = {
  key: string;
  configured: boolean;
  last_error: string | null;
  last_error_at: string | null;
};

const LABELS: Record<string, string> = {
  leadmagic: 'LeadMagic',
  findymail: 'Findymail',
  findymail_verify: 'Findymail (verify)',
  prospeo: 'Prospeo',
  pdl_company: 'PDL (company)',
  pdl_person: 'PDL (person search)',
};
const ORDER_KEYS = ['leadmagic', 'findymail', 'prospeo'];
const usd = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const when = (iso: string) =>
  new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC';

/**
 * The worker is the only place a key is ever seen. It answers with booleans and short error text,
 * never the key itself; the same base URL serves reveal and status behind WORKER_STATUS_TOKEN.
 */
async function workerStatus(): Promise<{ rows: Map<string, WorkerRow>; error: boolean }> {
  const base = process.env.WORKER_REVEAL_URL?.trim().replace(/\/+$/, '');
  const token = process.env.WORKER_STATUS_TOKEN?.trim();
  if (!base || !token) return { rows: new Map(), error: false };
  try {
    const res = await fetch(`${base}/providers/status`, {
      headers: { authorization: `Bearer ${token}` },
      cache: 'no-store',
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return { rows: new Map(), error: true };
    const payload = (await res.json()) as { providers?: WorkerRow[] };
    const list = Array.isArray(payload.providers) ? payload.providers : [];
    return { rows: new Map(list.filter((r) => r && typeof r.key === 'string').map((r) => [r.key, r])), error: false };
  } catch {
    return { rows: new Map(), error: true };
  }
}

/** Settings → Providers: read-only status, prices and spend; only the waterfall order is editable. */
export async function Providers() {
  const ctx = await getOrgContext();
  const supabase = await createClient();
  if (!ctx?.workspace || !supabase) return null;
  const ws = ctx.workspace.id;
  const canEdit = atLeast(ctx.workspaceRole, 'Admin');

  const [{ data, error }, settings, worker] = await Promise.all([
    supabase.rpc('provider_status', { p_ws: ws }),
    supabase.from('workspace_settings').select('provider_order').eq('workspace_id', ws).maybeSingle(),
    workerStatus(),
  ]);
  const dbRows = (Array.isArray(data) ? data : []) as DbRow[];
  const prices = new Map(dbRows.map((r) => [r.key, r]));
  const saved = (settings as { provider_order?: unknown } | null)?.provider_order;
  const order = Array.isArray(saved)
    ? [...saved.filter((k): k is string => typeof k === 'string' && ORDER_KEYS.includes(k))]
    : [];
  for (const key of ORDER_KEYS) if (!order.includes(key)) order.push(key);

  const stateOf = (key: string) => {
    const db = prices.get(key);
    const w = worker.rows.get(key);
    const lastError = db?.last_error || w?.last_error;
    if (lastError) {
      const at = db?.last_error_at || w?.last_error_at;
      return { label: 'Failing', detail: `${lastError}${at ? ` · ${when(at)}` : ''}` };
    }
    if (w?.configured) return { label: 'Connected', detail: null };
    return { label: 'Not set up yet', detail: null };
  };

  return (
    <section className="si-card" aria-label="Providers" data-providers="">
      <div className="flex flex-col gap-0.5">
        <h2 className="si-h2">Providers</h2>
        <p className="m-0 max-w-[70ch] text-[13px] text-ink-muted">
          Keys are set on the worker, never on this page. Status is read-only; prices are per call and spend is for this workspace this month (UTC).
        </p>
      </div>
      {error ? <p role="alert" className="m-0 text-[13px] text-danger" data-provider-status-error="">The provider ledger could not be loaded.</p> : null}
      {worker.error ? (
        <p role="alert" className="m-0 text-[13px] text-ink-muted" data-worker-status-error="">
          Worker status is unavailable right now; treat every provider as not set up yet until it returns.
        </p>
      ) : null}
      <div className="overflow-x-auto">
        <table className="si-table">
          <thead>
            <tr className="border-b border-line">
              {['Provider', 'Status', 'Price', 'This month', 'Attempts'].map((h) => (
                <th key={h} className="si-label px-2 py-2 font-semibold">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {dbRows.map((row) => {
              const state = stateOf(row.key);
              return (
                <tr key={row.key} className="border-b border-line align-top last:border-b-0" data-provider-row={row.key}>
                  <td className="px-2 py-2 font-semibold">{LABELS[row.key] ?? row.key}</td>
                  <td className="px-2 py-2">
                    <span className="si-chip" data-provider-state="">{state.label}</span>
                    {state.detail ? <span className="block text-[12px] text-ink-muted" data-provider-error="">{state.detail}</span> : null}
                  </td>
                  <td className="px-2 py-2 whitespace-nowrap" data-provider-price="">{usd(Number(row.price_usd))} / call</td>
                  <td className="px-2 py-2 whitespace-nowrap" data-provider-spend="">{usd(Number(row.month_spend_usd))} this month</td>
                  <td className="px-2 py-2">{Number(row.attempts ?? 0)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex flex-col gap-2" data-waterfall-order="">
        <h3 className="m-0 text-[15px] font-semibold">Waterfall order</h3>
        <p className="m-0 text-[13px] text-ink-muted">
          {canEdit ? 'Admins can move a provider up or down. Everyone else sees the order read-only.' : 'The order is read-only for your role.'}
        </p>
        <ol className="m-0 flex flex-col gap-1 p-0">
          {order.map((key, i) => (
            <li key={key} className="flex items-center justify-between gap-2 rounded-md border border-line px-3 py-2">
              <span className="font-semibold" data-order-key={key}>{LABELS[key] ?? key}</span>
              {canEdit ? (
                <span className="flex items-center gap-1">
                  <form action={moveProvider}>
                    <input type="hidden" name="ws" value={ws} />
                    <input type="hidden" name="order" value={JSON.stringify(order)} />
                    <input type="hidden" name="from" value={i} />
                    <input type="hidden" name="delta" value={-1} />
                    <button type="submit" className="si-btn si-btn-sm" aria-label={`Move ${LABELS[key] ?? key} up`} disabled={i === 0}>↑</button>
                  </form>
                  <form action={moveProvider}>
                    <input type="hidden" name="ws" value={ws} />
                    <input type="hidden" name="order" value={JSON.stringify(order)} />
                    <input type="hidden" name="from" value={i} />
                    <input type="hidden" name="delta" value={1} />
                    <button type="submit" className="si-btn si-btn-sm" aria-label={`Move ${LABELS[key] ?? key} down`} disabled={i === order.length - 1}>↓</button>
                  </form>
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
