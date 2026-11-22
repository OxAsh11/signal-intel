'use server';

import { refresh } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { friendlyError } from '@/lib/org';

/** The cell panel's server actions. Each calls an audited SQL function that re-checks the caller's role. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const REASON_MAX = 500;

export type CellDetail = {
  column: { id: string; key: string; label: string; value_type: string; fill_method: string | null; auto_run: boolean | null };
  state: string;
  value: unknown;
  confidence: number | null;
  fill_label: string;
  claims: {
    id: string;
    value: unknown;
    quote: string | null;
    source_url: string | null;
    source_type: string | null;
    observed_at: string | null;
    status: 'active' | 'superseded' | 'retracted';
    superseded_at: string | null;
  }[];
  inputs: { column_label: string; value: unknown; claim_id: string | null }[];
  runs: {
    id: string;
    status: string;
    cost_usd: number | null;
    brief_version: number | null;
    queued_at: string | null;
    started_at: string | null;
    finished_at: string | null;
    error: string | null;
  }[];
};

export type Estimate = { count: number; cost_usd: number; within_budget: number };
export type CellResult<T = undefined> = { ok: true; data: T } | { ok: false; error: string; code?: string };

async function db() {
  return createClient();
}
const fail = (message: string | undefined, code?: string): { ok: false; error: string; code?: string } => ({ ok: false, error: friendlyError(message), code });
const NOT_CONFIGURED = { ok: false as const, error: 'Supabase is not configured.' };

/** Reads one cell's panel. Never runs anything. */
export async function loadCellDetail(ws: string, entity: string, predicate: string): Promise<CellResult<CellDetail>> {
  if (![ws, entity, predicate].every((x) => UUID.test(x))) return { ok: false, error: 'That cell was not found.' };
  const supabase = await db();
  if (!supabase) return NOT_CONFIGURED;
  const { data, error } = await supabase.rpc('cell_detail', { p_ws: ws, p_entity: entity, p_predicate: predicate });
  if (error || !data) return fail(error?.message);
  return { ok: true, data: data as unknown as CellDetail };
}

/** What re-running this one cell would cost. Reads only. */
export async function estimateCellRerun(ws: string, entity: string, predicate: string, brief: string | null): Promise<CellResult<Estimate>> {
  if (![ws, entity, predicate].every((x) => UUID.test(x)) || (brief && !UUID.test(brief))) return { ok: false, error: 'That cell was not found.' };
  const supabase = await db();
  if (!supabase) return NOT_CONFIGURED;
  const { data, error } = await supabase.rpc('estimate_column_run', {
    p_ws: ws,
    p_predicate: predicate,
    p_mode: 'selected',
    p_brief: brief,
    p_entity_ids: [entity],
  });
  if (error || !data) return fail(error?.message);
  return { ok: true, data: data as unknown as Estimate };
}

/** Queues the run. Only called after the person confirmed the estimate. */
export async function rerunCell(ws: string, entity: string, predicate: string, brief: string | null): Promise<CellResult<{ queued: number }>> {
  if (![ws, entity, predicate].every((x) => UUID.test(x)) || (brief && !UUID.test(brief))) return { ok: false, error: 'That cell was not found.' };
  const supabase = await db();
  if (!supabase) return NOT_CONFIGURED;
  const { data, error } = await supabase.rpc('request_column_run', {
    p_ws: ws,
    p_predicate: predicate,
    p_mode: 'selected',
    p_brief: brief,
    p_entity_ids: [entity],
  });
  if (error) return fail(error.message, error.code);
  refresh();
  return { ok: true, data: { queued: Number((data as { queued?: number } | null)?.queued ?? 0) } };
}

/** Writes a person's value. `value` is already typed (boolean, number or string); SQL validates it again. */
export async function enterCellValue(ws: string, entity: string, predicate: string, value: boolean | number | string): Promise<CellResult> {
  if (![ws, entity, predicate].every((x) => UUID.test(x))) return { ok: false, error: 'That cell was not found.' };
  const supabase = await db();
  if (!supabase) return NOT_CONFIGURED;
  const { error } = await supabase.rpc('set_manual_value', { p_ws: ws, p_entity: entity, p_predicate: predicate, p_value: value });
  if (error) return fail(error.message, error.code);
  refresh();
  return { ok: true, data: undefined };
}

/** Marks a claim wrong: it is withdrawn and a Review item carries the reason. */
export async function markCellWrong(ws: string, claim: string, reason: string): Promise<CellResult> {
  const why = reason.trim();
  if (!UUID.test(ws) || !UUID.test(claim)) return { ok: false, error: 'That value was not found.' };
  if (!why) return { ok: false, error: 'Say why this value is wrong.' };
  if (why.length > REASON_MAX) return { ok: false, error: `The reason can be at most ${REASON_MAX} characters.` };
  const supabase = await db();
  if (!supabase) return NOT_CONFIGURED;
  const { error } = await supabase.rpc('mark_claim_wrong', { p_ws: ws, p_claim: claim, p_reason: why });
  if (error) return fail(error.message, error.code);
  refresh();
  return { ok: true, data: undefined };
}
