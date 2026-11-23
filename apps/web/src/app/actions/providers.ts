'use server';

import { refresh } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { friendlyError } from '@/lib/org';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const KNOWN = ['leadmagic', 'findymail', 'prospeo'];

/**
 * Waterfall reordering from Settings. The SQL function re-checks the Admin workspace role; here
 * we only shape the request so a crafted form cannot move an unknown provider or duplicate one.
 */
export async function moveProvider(form: FormData): Promise<void> {
  const ws = String(form.get('ws') ?? '');
  const order = JSON.parse(String(form.get('order') ?? '[]')) as unknown;
  const from = Number(form.get('from'));
  const delta = Number(form.get('delta'));
  const to = from + delta;
  if (!UUID.test(ws) || !Array.isArray(order) || order.length !== KNOWN.length) return;
  if (!order.every((k) => typeof k === 'string' && KNOWN.includes(k))) return;
  if (!Number.isInteger(from) || !Number.isInteger(delta) || Math.abs(delta) !== 1) return;
  if (to < 0 || to >= order.length) return;
  const next = [...order] as string[];
  [next[from], next[to]] = [next[to]!, next[from]!];

  const supabase = await createClient();
  if (!supabase) return;
  const { error } = await supabase.rpc('set_provider_order', { p_ws: ws, p_order: next });
  if (error) {
    console.error(`set_provider_order failed for workspace ${ws}: ${friendlyError(error.message)}`);
    return;
  }
  refresh();
}
