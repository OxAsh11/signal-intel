import 'server-only';
import { unstable_rethrow } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';

/** What every Home card is given: the workspace, the lens (a Brief id, or null for all) and the Retry link. */
export type HomeCardProps = { ws: string; brief: string | null; retry: string };

type HomeRpc =
  | 'home_header'
  | 'home_journey'
  | 'home_metrics'
  | 'home_needs_you'
  | 'home_accounts_to_act'
  | 'home_changes'
  | 'home_crm'
  | 'home_sources';

/**
 * Reads one Home card's RPC as the signed-in user. Returns null when it fails (the error is
 * logged), so the card renders `CardError` and the rest of Home still renders.
 */
export async function loadCard<T>(fn: HomeRpc, { ws, brief }: HomeCardProps): Promise<T | null> {
  try {
    const supabase = await createClient();
    if (!supabase) return null;
    const { data, error } = await supabase.rpc(fn, { p_ws: ws, p_brief: brief });
    if (error || data == null) {
      console.error(`home: ${fn} failed for workspace ${ws}: ${error?.message ?? 'no data'}`);
      return null;
    }
    return data as T;
  } catch (e) {
    unstable_rethrow(e);
    console.error(`home: ${fn} threw for workspace ${ws}: ${e instanceof Error ? e.message : String(e)}`);
    return null;
  }
}

const nf = new Intl.NumberFormat('en');
const money = new Intl.NumberFormat('en', { style: 'currency', currency: 'USD' });
const dayFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });

/** A count as the page shows it: `1,204`. Missing values read as 0 (the RPCs never return null counts). */
export const fmt = (n: number | string | null | undefined) => nf.format(Number(n ?? 0));
/** Money as `$12.40`; a positive amount under a cent reads `< $0.01`. */
export function usd(n: number | string | null | undefined): string {
  const v = Number(n ?? 0);
  return v > 0 && v < 0.005 ? '< $0.01' : money.format(v);
}
/** A date as `17 Nov` (UTC). */
export const day = (iso: string) => dayFmt.format(new Date(iso));
export const plural = (n: number | string, one: string, many: string) => (Number(n) === 1 ? one : many);
