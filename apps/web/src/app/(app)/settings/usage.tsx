import Link from 'next/link';
import { Button } from '@signal/ui';
import { decideApproval, setBudget } from '@/app/actions/budgets';
import { ActionForm, inputClass } from '@/components/forms';
import { NotConfigured } from '@/components/not-configured';
import { atLeast, getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';

type AiStatus = { chat?: boolean; embed?: boolean; checked_at?: string | null };
type LedgerRow = {
  created_at: string;
  capability: string;
  model: string | null;
  unit: string;
  quantity: number;
  cost_usd: number;
  status: string;
};
type ApprovalRow = { id: string; job_type: string; estimate_usd: number; reason: string; requested_at: string };

const DEFAULT_EDITOR_LIMIT = 25; // matches the SQL default when no workspace_settings row is readable
const LEDGER_LIMIT = 200;

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-md border border-line bg-surface p-4">
      <div className="flex flex-col gap-0.5">
        <h2 className="m-0 text-[15px] leading-[22px] font-semibold">{title}</h2>
        {hint ? <p className="m-0 max-w-[70ch] text-[13px] text-ink-muted">{hint}</p> : null}
      </div>
      {children}
    </section>
  );
}

const usd = (n: number) => n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
/** Per-row costs under one cent need four decimals to show anything. */
const rowUsd = (n: number) =>
  n > 0 && n < 0.01
    ? n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 4, maximumFractionDigits: 4 })
    : usd(n);
const when = (iso: string) =>
  new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC';

/** Day and month periods are UTC calendar periods. */
function periodStart(period: 'day' | 'month', now = new Date()): string {
  return period === 'day'
    ? new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())).toISOString()
    : new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString();
}

export async function UsageAndBudgets({ searchParams }: { searchParams: Promise<{ [key: string]: string | string[] | undefined }> }) {
  const ctx = await getOrgContext();
  const supabase = await createClient();
  if (!ctx?.org || !ctx.workspace || !supabase) return null;
  const params = await searchParams;
  const ledgerParam = Array.isArray(params.ledger) ? params.ledger[0] : params.ledger;
  const ledgerPeriod = ledgerParam === 'day' || ledgerParam === 'month' ? ledgerParam : null;

  const wsId = ctx.workspace.id;
  const isAdmin = atLeast(ctx.orgRole, 'Admin');
  const isEditor = atLeast(ctx.workspaceRole, 'Editor');
  const now = new Date();
  const monthStart = periodStart('month', now);
  const dayStart = periodStart('day', now);

  const [{ data: statusData }, { data: budgetData }, { data: spendData }, { data: settingsData }, { data: approvalData }, ledger] =
    await Promise.all([
      supabase.rpc('ai_status'),
      supabase.from('budget').select('period, cap_usd').eq('workspace_id', wsId).is('brief_id', null),
      // Open reservations are excluded from the displayed figure; only settled (ok, rejected) rows count.
      supabase
        .from('usage_ledger')
        .select('cost_usd, created_at')
        .eq('workspace_id', wsId)
        .in('status', ['ok', 'rejected'])
        .gte('created_at', monthStart)
        .limit(10000),
      isEditor
        ? supabase.from('workspace_settings').select('editor_approval_limit_usd').eq('workspace_id', wsId).maybeSingle()
        : Promise.resolve({ data: null }),
      isEditor
        ? supabase
            .from('budget_approval')
            .select('id, job_type, estimate_usd, reason, requested_at')
            .eq('workspace_id', wsId)
            .eq('status', 'pending')
            .order('requested_at')
        : Promise.resolve({ data: [] }),
      ledgerPeriod
        ? supabase
            .from('usage_ledger')
            .select('created_at, capability, model, unit, quantity, cost_usd, status')
            .eq('workspace_id', wsId)
            .gte('created_at', periodStart(ledgerPeriod, now))
            .order('created_at', { ascending: false })
            .limit(LEDGER_LIMIT)
        : Promise.resolve({ data: [] }),
    ]);

  const status = (statusData ?? {}) as AiStatus;
  const caps = new Map(((budgetData ?? []) as { period: string; cap_usd: number }[]).map((b) => [b.period, Number(b.cap_usd)]));
  const spendRows = (spendData ?? []) as { cost_usd: number; created_at: string }[];
  const monthSpend = spendRows.reduce((s, r) => s + Number(r.cost_usd), 0);
  const daySpend = spendRows.filter((r) => r.created_at >= dayStart).reduce((s, r) => s + Number(r.cost_usd), 0);
  const editorLimit = Number((settingsData as { editor_approval_limit_usd?: number } | null)?.editor_approval_limit_usd ?? DEFAULT_EDITOR_LIMIT);
  const approvals = (approvalData ?? []) as ApprovalRow[];
  const ledgerRows = (ledger.data ?? []) as LedgerRow[];
  const capText = (p: 'day' | 'month') => (caps.has(p) ? usd(caps.get(p)!) : 'No cap');

  const periods = [
    { key: 'day' as const, label: 'Today', spend: daySpend },
    { key: 'month' as const, label: 'This month', spend: monthSpend },
  ];

  return (
    <>
      <Section title="AI providers" hint="Whether the worker can reach each AI provider. Keys live in the environment, never on this page.">
        <div className="grid gap-3 sm:grid-cols-2">
          {status.chat ? (
            <div className="flex items-center justify-between gap-2 rounded-md border border-line p-3">
              <span className="font-semibold">Claude (extract, judge, write)</span>
              <span className="si-chip">Set up</span>
            </div>
          ) : (
            <NotConfigured name="Claude (extract, judge, write)" setup={['Add ANTHROPIC_API_KEY to the environment and restart the worker.']} />
          )}
          {status.embed ? (
            <div className="flex items-center justify-between gap-2 rounded-md border border-line p-3">
              <span className="font-semibold">Voyage (embeddings)</span>
              <span className="si-chip">Set up</span>
            </div>
          ) : (
            <NotConfigured name="Voyage (embeddings)" setup={['Add VOYAGE_API_KEY to the environment and restart the worker.']} />
          )}
        </div>
        {!status.checked_at ? <p className="m-0 text-[13px] text-ink-muted">The worker hasn&apos;t reported yet.</p> : null}
      </Section>

      <Section
        title="Spend"
        hint="Totals for this workspace in UTC days and months. Work still in progress is not counted until it settles. Select a figure to see the rows behind it."
      >
        <div className="grid gap-3 sm:grid-cols-2">
          {periods.map((p) => (
            <div key={p.key} className="flex flex-col gap-0.5 rounded-md border border-line p-3">
              <span className="si-label">{p.label}</span>
              <Link href={`?ledger=${p.key}`} scroll={false} className="text-[22px] font-semibold text-ink underline decoration-line-strong underline-offset-4">
                {usd(p.spend)}
              </Link>
              <span className="text-[13px] text-ink-muted">Cap: {capText(p.key)}</span>
            </div>
          ))}
        </div>
        {ledgerPeriod ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <span className="si-label">{ledgerPeriod === 'day' ? 'Rows for today' : 'Rows for this month'}</span>
              <Link href="?" scroll={false} className="text-[13px] text-ink-muted underline">
                Hide rows
              </Link>
            </div>
            {ledgerRows.length === 0 ? (
              <p className="m-0 text-ink-muted">No usage in this period.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-left">
                  <thead>
                    <tr className="border-b border-line">
                      {['Time', 'Capability', 'Model', 'Unit', 'Quantity', 'Cost', 'Status'].map((h) => (
                        <th key={h} className="si-label px-2 py-2 font-semibold">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {ledgerRows.map((r, i) => (
                      <tr key={`${r.created_at}-${i}`} className="border-b border-line last:border-b-0">
                        <td className="px-2 py-2 whitespace-nowrap">{when(r.created_at)}</td>
                        <td className="px-2 py-2">{r.capability}</td>
                        <td className="px-2 py-2">{r.model ?? 'None'}</td>
                        <td className="px-2 py-2">{r.unit}</td>
                        <td className="px-2 py-2">{Number(r.quantity).toLocaleString('en-US')}</td>
                        <td className="px-2 py-2">{rowUsd(Number(r.cost_usd))}</td>
                        <td className="px-2 py-2">
                          <span className="si-chip">{r.status}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {ledgerRows.length >= LEDGER_LIMIT ? <p className="m-0 text-[13px] text-ink-muted">Showing the newest {LEDGER_LIMIT} rows.</p> : null}
          </div>
        ) : null}
      </Section>

      <Section
        title="Budgets"
        hint="Caps apply to the whole workspace. When work would pass a cap it pauses and waits for approval. Leave a field empty for no cap."
      >
        {isAdmin ? (
          <ActionForm action={setBudget} className="flex flex-wrap items-end gap-3" successText="Saved.">
            <input type="hidden" name="workspace" value={wsId} />
            <label className="flex flex-col gap-1">
              <span className="si-label">Daily cap (USD)</span>
              <input name="day" inputMode="decimal" className={inputClass} defaultValue={caps.get('day') ?? ''} placeholder="No cap" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="si-label">Monthly cap (USD)</span>
              <input name="month" inputMode="decimal" className={inputClass} defaultValue={caps.get('month') ?? ''} placeholder="No cap" />
            </label>
            <Button type="submit">Save budgets</Button>
          </ActionForm>
        ) : (
          <>
            <p className="m-0">Daily cap: {capText('day')}</p>
            <p className="m-0">Monthly cap: {capText('month')}</p>
            <p className="m-0 text-[13px] text-ink-muted">Only Owners and Admins can change budgets.</p>
          </>
        )}
      </Section>

      {isEditor ? (
        <Section
          title="Waiting for approval"
          hint={`Work paused by a cap. Editors can approve up to ${usd(editorLimit)}; Owners and Admins can approve any amount.`}
        >
          {approvals.length === 0 ? (
            <p className="m-0 text-ink-muted">Nothing is waiting for approval.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {approvals.map((a) => {
                const estimate = Number(a.estimate_usd);
                const canApprove = isAdmin || estimate <= editorLimit;
                return (
                  <div key={a.id} className="flex flex-col gap-2 border-t border-line pt-2 first:border-t-0 first:pt-0">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className="font-semibold">{a.job_type}</span>
                      <span>{usd(estimate)} estimated</span>
                    </div>
                    <p className="m-0 text-[13px] text-ink-muted">
                      {a.reason} · requested {when(a.requested_at)}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {canApprove ? (
                        <ActionForm action={decideApproval}>
                          <input type="hidden" name="approval" value={a.id} />
                          <input type="hidden" name="decision" value="approve" />
                          <Button type="submit" size="sm">
                            Approve
                          </Button>
                        </ActionForm>
                      ) : (
                        <span className="text-[12px] text-ink-muted">Above your limit: ask an Admin or Owner to approve.</span>
                      )}
                      {/* The SQL sets no amount limit on rejecting, so any Editor can reject. */}
                      <ActionForm action={decideApproval}>
                        <input type="hidden" name="approval" value={a.id} />
                        <input type="hidden" name="decision" value="reject" />
                        <Button type="submit" size="sm" variant="quiet">
                          Reject
                        </Button>
                      </ActionForm>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Section>
      ) : null}
    </>
  );
}
