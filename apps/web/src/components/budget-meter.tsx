import Link from 'next/link';
import { cx } from '@signal/ui';
import { getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';

type Meter = { spent_usd: number | string | null; cap_usd: number | string | null; pct: number | string | null };

const usd = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });

/**
 * AI spend this month against the workspace's monthly cap, from `public.budget_meter` (the sum the
 * budget guard uses). Mustard at 80% or more, lava at 100% or more; `· no cap` when no monthly cap
 * is set. Links to Settings, where budgets are changed. Shows a muted "—" (title "Couldn't load") if the read fails.
 */
export async function BudgetMeter({ className }: { className?: string }) {
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;
  const ws = ctx.workspace.id;
  const { data, error } = await supabase.rpc('budget_meter', { p_ws: ws });
  if (error || !data) {
    // A failed read is shown, not swallowed: a muted dash that says so (the shell still renders).
    if (error) console.error(`budget meter: failed for workspace ${ws}: ${error.message}`);
    return (
      <span title="Couldn't load" className="block px-2.5 py-2 font-mono text-[13px] text-ink-muted">
        —
      </span>
    );
  }
  const m = data as Meter;
  const spent = Number(m.spent_usd ?? 0);
  const cap = m.cap_usd == null ? null : Number(m.cap_usd);
  const pct = m.pct == null ? null : Number(m.pct);
  // The colour comes from the unrounded ratio, not the one-decimal pct: 79.96% rounds to 80.0
  // but is not yet at 80%.
  const ratio = cap == null ? null : spent / cap; // cap_usd > 0 (budget check)
  const level = ratio == null ? 'none' : ratio >= 1 ? 'over' : ratio >= 0.8 ? 'warn' : 'ok';
  const family = level === 'over' ? 'si-fam-lava' : level === 'warn' ? 'si-fam-mustard' : null;

  return (
    <Link
      href="/settings"
      data-level={level}
      title={pct == null ? 'AI spend this month; no monthly cap is set' : `AI spend this month: ${pct}% of the monthly cap`}
      className={cx(
        'flex flex-col gap-0.5 rounded-[4px] px-2.5 py-2 no-underline hover:no-underline',
        family ? `${family} bg-[var(--f02)] text-[var(--f09)]` : 'text-ink hover:bg-surface-sunken',
        className,
      )}
    >
      <span className={cx('text-[12px] leading-4', family ? 'text-[var(--f09)]' : 'text-ink-muted')}>AI spend this month</span>
      <span className="font-mono text-[13px] leading-[18px] whitespace-nowrap">
        {cap == null ? `${usd(spent)} · no cap` : `${usd(spent)} / ${usd(cap)}`}
      </span>
    </Link>
  );
}
