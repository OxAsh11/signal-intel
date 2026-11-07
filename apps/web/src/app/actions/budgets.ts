'use server';

import { refresh } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { friendlyError } from '@/lib/org';
import type { ActionState } from './access';

/** Every write goes through an audited SQL function; the database re-checks the caller's role. */
async function rpc(fn: string, args: Record<string, unknown>) {
  const supabase = await createClient();
  if (!supabase) return { error: 'Supabase is not configured.' };
  const { error } = await supabase.rpc(fn, args);
  return { error: error ? friendlyError(error.message) : null };
}

const text = (form: FormData, key: string) => String(form.get(key) ?? '').trim();

/** An empty input removes the cap (null); otherwise a positive USD amount. */
function parseCap(raw: string, label: string): { cap: number | null; error?: string } {
  if (raw === '') return { cap: null };
  const n = Number(raw.replace(/^\$/, ''));
  if (!Number.isFinite(n) || n <= 0) return { cap: null, error: `Enter a ${label} cap above 0, or leave it empty for no cap.` };
  return { cap: Math.round(n * 100) / 100 };
}

/** Workspace-level day and month caps. Brief caps arrive with Brief Studio. */
export async function setBudget(_: ActionState, form: FormData): Promise<ActionState> {
  const workspace = text(form, 'workspace');
  if (!workspace) return { error: 'Choose a workspace first.' };
  const day = parseCap(text(form, 'day'), 'daily');
  if (day.error) return { error: day.error };
  const month = parseCap(text(form, 'month'), 'monthly');
  if (month.error) return { error: month.error };
  for (const [period, cap] of [
    ['day', day.cap],
    ['month', month.cap],
  ] as const) {
    const { error } = await rpc('set_budget', { p_ws: workspace, p_brief: null, p_period: period, p_cap: cap });
    if (error) return { error };
  }
  refresh();
  return { ok: true };
}

export async function decideApproval(_: ActionState, form: FormData): Promise<ActionState> {
  const id = text(form, 'approval');
  if (!id) return { error: 'That approval was not found.' };
  const { error } = await rpc('decide_budget_approval', { p_id: id, p_approve: text(form, 'decision') === 'approve' });
  if (error) return { error };
  refresh();
  return { ok: true };
}
