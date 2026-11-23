'use server';

import { refresh } from 'next/cache';
import { getBriefLens } from '@/lib/brief-lens';
import { friendlyError } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';

/**
 * Views, pins and search for the accounts table. Every write is an audited SQL function that
 * re-checks the caller's role (a Viewer saves personal views and pins; a workspace view needs an
 * Editor, and the database refuses it with 42501 even if the option were forced).
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const PRESETS = ['ready_to_contact', 'needs_evidence', 'stale', 'errored', 'by_verdict'];
const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);

export type ViewResult<T = undefined> = { ok: true; data: T } | { ok: false; error: string; code?: string };
const fail = (message: string | undefined, code?: string): { ok: false; error: string; code?: string } => ({ ok: false, error: friendlyError(message), code });
const NOT_CONFIGURED = { ok: false as const, error: 'Supabase is not configured.' };

export type SaveViewInput = {
  name: string;
  visibility: 'personal' | 'workspace';
  /** The Brief the view belongs to; null for all Briefs. */
  brief: string | null;
  filter: Record<string, unknown> | null;
  columns: { kind: 'predicate'; ref: string }[];
  sort: unknown[];
  layout: 'table' | 'board' | 'map';
};

/** Saves the current filter, columns, sort and layout as a new view; returns its id. */
export async function saveView(ws: string, input: SaveViewInput): Promise<ViewResult<{ id: string }>> {
  const name = String(input?.name ?? '').trim();
  if (!UUID.test(ws)) return { ok: false, error: 'That workspace was not found.' };
  if (name.length < 1 || name.length > 120) return { ok: false, error: 'Name the view in 1 to 120 characters.' };
  if (input.visibility !== 'personal' && input.visibility !== 'workspace') return { ok: false, error: 'Choose personal or workspace.' };
  if (input.brief !== null && !UUID.test(input.brief)) return { ok: false, error: 'That Brief was not found.' };
  if (input.filter !== null && !isObject(input.filter)) return { ok: false, error: 'The filter is not valid.' };
  if (!Array.isArray(input.columns) || !Array.isArray(input.sort)) return { ok: false, error: 'The view is not valid.' };
  if (!['table', 'board', 'map'].includes(input.layout)) return { ok: false, error: 'The layout is not valid.' };
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const { data, error } = await supabase.rpc('save_view', {
    p_ws: ws,
    p_name: name,
    p_brief: input.brief ?? undefined,
    p_columns: input.columns,
    p_filter: input.filter ?? {},
    p_sort: input.sort,
    p_layout: input.layout,
    p_visibility: input.visibility,
  });
  if (error || typeof data !== 'string') return fail(error?.message, error?.code);
  refresh();
  return { ok: true, data: { id: data } };
}

/** Deletes a saved view (its creator, or an Admin). */
export async function deleteView(id: string): Promise<ViewResult> {
  if (!UUID.test(id)) return { ok: false, error: 'That view was not found.' };
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const { error } = await supabase.rpc('delete_view', { p_id: id });
  if (error) return fail(error.message, error.code);
  refresh();
  return { ok: true, data: undefined };
}

/** Pins or unpins a view (a preset name or a saved view id) in this member's sidebar. */
export async function pinView(ws: string, view: string, pinned: boolean): Promise<ViewResult> {
  if (!UUID.test(ws) || !(UUID.test(view) || PRESETS.includes(view))) return { ok: false, error: 'That view was not found.' };
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const { error } = await supabase.rpc('pin_view', { p_ws: ws, p_view: view, p_pinned: pinned });
  if (error) return fail(error.message, error.code);
  refresh();
  return { ok: true, data: undefined };
}

export type SearchHit = { account_id: string; name: string; domain: string | null; href: string };

/** The top-bar search: up to 8 accounts whose name or domain contains `q` (the server reads % and _ literally). */
export async function searchAccounts(ws: string, q: string): Promise<ViewResult<SearchHit[]>> {
  if (!UUID.test(ws)) return { ok: false, error: 'That workspace was not found.' };
  const text = String(q ?? '').slice(0, 200);
  if (text.trim().length < 2) return { ok: true, data: [] };
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const lens = await getBriefLens();
  const { data, error } = await supabase.rpc('search_accounts', { p_ws: ws, p_q: text, p_brief: lens.briefId ?? undefined });
  if (error) return fail(error.message, error.code);
  return { ok: true, data: (Array.isArray(data) ? data : []) as SearchHit[] };
}
