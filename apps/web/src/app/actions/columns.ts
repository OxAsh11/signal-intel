'use server';

import { refresh } from 'next/cache';
import { formulaProblem, formulaReads } from '@signal/formula';
import { createClient } from '@/lib/supabase/server';
import { friendlyError } from '@/lib/org';
import {
  columnKeyProblem,
  columnSpecs,
  isColumnSpec,
  keyFromLabel,
  MAX_COLUMNS,
  PENDING_CHANGES,
  TYPE_LABELS,
  type BriefConfig,
  type ColumnSpec,
  type ValueType,
} from '@/lib/briefs';

/**
 * The operating table's column actions: Add column (the Brief's draft → P2 preview → publish),
 * run requests (P3 estimate_column_run / request_column_run) and the formula auto-run switch.
 * Every write is an audited SQL function that re-checks the caller's role (Viewers get 42501).
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const PREVIEWABLE = ['Pilot', 'Active', 'Paused'];
const MODES = ['empty', 'stale', 'errored', 'selected'] as const;
const MAX_SELECTED = 1000;

export type RunMode = (typeof MODES)[number];
export type Result<T = undefined> = { ok: true; data: T } | { ok: false; error: string; code?: string; firstN?: number };

/** public.estimate_column_run. */
export type RunEstimate = {
  count: number;
  per_entity_usd: number;
  cost_usd: number;
  remaining_usd: number | null;
  within_budget: number;
  max_per_request: number;
};

/** What the Add column form needs to check a new column as the database will. */
export type AddColumnContext = {
  state: string;
  /**
   * R9: the published Brief has other unpublished draft changes. Add column refuses until they are
   * published or discarded on the Brief page, so nobody's half-finished edits publish by accident.
   */
  pending: boolean;
  specs: Pick<ColumnSpec, 'key' | 'label' | 'value_type' | 'fill_method'>[];
  legacyKeys: string[];
  workspace: { key: string; value_type: string; fill_method: string | null }[];
};

export type NewColumn = {
  kind: 'extract' | 'formula';
  label: string;
  value_type: ValueType;
  allowed_values: string[];
  hint: string;
  formula: string;
};

/** The P2 preview of the draft, as the Add column form shows it. */
export type ColumnPreview = {
  id: string;
  status: 'pending' | 'ready' | 'failed';
  tier: string | null;
  estimate_usd: number;
  duration_minutes: number | null;
  over_budget: boolean;
  /** The P2 backfill scope count: records already collected that a new extract column reads. */
  records: number;
  approval: string | null;
};

const NOT_CONFIGURED = { ok: false as const, error: 'Supabase is not configured.' };
/** R9's refusal: the code is 55000 (object not in prerequisite state), as the database would say. */
const pendingRefusal = { ok: false as const, error: PENDING_CHANGES, code: '55000' };

/** JSON with object keys sorted, so two configs compare by content (jsonb reorders keys). */
function canonical(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonical).join(',')}]`;
  if (v !== null && typeof v === 'object') {
    const o = v as Record<string, unknown>;
    return `{${Object.keys(o)
      .filter((k) => o[k] !== undefined)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonical(o[k])}`)
      .join(',')}}`;
  }
  return JSON.stringify(v ?? null);
}
const sameConfig = (a: unknown, b: unknown) => canonical(a) === canonical(b);
const fail = (message: string | undefined, code?: string) => ({ ok: false as const, error: friendlyError(message), code });

async function loadBrief(ws: string, brief: string) {
  const supabase = await createClient();
  if (!supabase) return { supabase: null, brief: null, config: null, published: null, pending: false } as const;
  const { data } = await supabase
    .from('brief')
    .select('id, state, draft_config, current_version_id')
    .eq('id', brief)
    .eq('workspace_id', ws)
    .maybeSingle();
  const b = data as { id: string; state: string; draft_config: BriefConfig | null; current_version_id: string | null } | null;
  let published: BriefConfig | null = null;
  if (b?.current_version_id) {
    const { data: ver } = await supabase.from('brief_version').select('config').eq('id', b.current_version_id).maybeSingle();
    published = ((ver as { config: BriefConfig } | null)?.config ?? {}) as BriefConfig;
  }
  const config: BriefConfig = b?.draft_config ?? published ?? {};
  // R9: a published Brief whose draft differs from its current version has someone's unpublished
  // changes. A Brief that was never published is all draft: a column just joins it.
  const pending = !!b && published !== null && b.draft_config != null && !sameConfig(b.draft_config, published);
  return { supabase, brief: b, config, published, pending } as const;
}

/** Reads only: the lens Brief's columns and the workspace's, so the form can check a column as you type. */
export async function loadAddColumnContext(ws: string, brief: string): Promise<Result<AddColumnContext>> {
  if (!UUID.test(ws) || !UUID.test(brief)) return { ok: false, error: 'Choose a Brief in the lens to add a column.' };
  const { supabase, brief: b, config, pending } = await loadBrief(ws, brief);
  if (!supabase) return NOT_CONFIGURED;
  if (!b) return { ok: false, error: 'That Brief was not found in this workspace.' };
  const { data: preds } = await supabase.from('predicate').select('key, value_type, fill_method').eq('workspace_id', ws);
  const all = Array.isArray(config.predicates) ? config.predicates : [];
  return {
    ok: true,
    data: {
      state: b.state,
      pending,
      specs: columnSpecs(config).map((s) => ({ key: s.key, label: s.label, value_type: s.value_type, fill_method: s.fill_method })),
      legacyKeys: all.filter((p): p is string => !isColumnSpec(p)),
      workspace: (preds ?? []) as AddColumnContext['workspace'],
    },
  };
}

/** The column spec the form describes, or what is wrong with it (the same checks the Brief editor makes). */
function buildSpec(input: NewColumn, config: BriefConfig, workspace: AddColumnContext['workspace']): { spec: ColumnSpec } | { error: string } {
  const label = String(input.label ?? '').trim();
  if (!label || label.length > 80) return { error: 'Give the column a name of 1 to 80 characters.' };
  if (!(input.value_type in TYPE_LABELS)) return { error: 'Choose what kind of value the column holds.' };
  if (input.kind !== 'extract' && input.kind !== 'formula') return { error: 'Choose how the column is filled.' };
  const all = Array.isArray(config.predicates) ? config.predicates : [];
  const specs = columnSpecs(config);
  if (all.length >= MAX_COLUMNS) return { error: `A Brief has at most ${MAX_COLUMNS} columns.` };
  const key = keyFromLabel(label, all.length + 1);
  let allowed: string[] | null = null;
  if (input.value_type === 'enum') {
    allowed = [...new Set((input.allowed_values ?? []).map((v) => String(v).trim()).filter(Boolean))];
    if (allowed.length < 2 || allowed.length > 12) return { error: 'A list column needs 2 to 12 values.' };
  }
  const spec: ColumnSpec = {
    key,
    label,
    value_type: input.value_type,
    allowed_values: allowed,
    extraction_hint: input.kind === 'extract' ? String(input.hint ?? '').trim().slice(0, 300) : '',
    decay_half_life_days: null,
    role: 'info',
    fill_method: input.kind,
  };
  if (input.kind === 'formula') {
    const formula = String(input.formula ?? '').trim();
    const legacy = all.filter((p): p is string => !isColumnSpec(p));
    const problem = formulaProblem(formula, key, [...specs.map((s) => s.key), key], legacy);
    if (problem) return { error: problem };
    spec.formula = formula;
    spec.inputs = formulaReads(formula) ?? [];
  }
  const keyProblem = columnKeyProblem(spec, specs, workspace.find((w) => w.key === key));
  if (keyProblem) return { error: keyProblem };
  return { spec };
}

/**
 * Add column, steps 1 and 2: appends the spec to the lens Brief's draft config
 * (save_brief_draft_ui) and asks for the P2 preview (request_brief_preview). A Draft Brief has no
 * published version to preview: the column waits in its draft until the pilot starts. R9: a
 * published Brief with other unpublished changes is refused (55000) until they are published or
 * discarded on the Brief page.
 */
export async function addColumn(
  ws: string,
  brief: string,
  input: NewColumn,
): Promise<Result<{ key: string; previewId: string | null }>> {
  if (!UUID.test(ws) || !UUID.test(brief)) return { ok: false, error: 'Choose a Brief in the lens to add a column.' };
  const { supabase, brief: b, config, pending } = await loadBrief(ws, brief);
  if (!supabase) return NOT_CONFIGURED;
  if (!b) return { ok: false, error: 'That Brief was not found in this workspace.' };
  if (b.state === 'Archived') return { ok: false, error: 'An archived Brief takes no new columns.' };
  if (pending) return pendingRefusal;
  const { data: preds } = await supabase.from('predicate').select('key, value_type, fill_method').eq('workspace_id', ws);
  const built = buildSpec(input, config, (preds ?? []) as AddColumnContext['workspace']);
  if ('error' in built) return { ok: false, error: built.error };

  const next: BriefConfig = { ...config, predicates: [...(config.predicates ?? []), built.spec] };
  const saved = await supabase.rpc('save_brief_draft_ui', { p_brief: brief, p_config: next });
  if (saved.error) return fail(saved.error.message, saved.error.code);
  if (!PREVIEWABLE.includes(b.state)) {
    refresh();
    return { ok: true, data: { key: built.spec.key, previewId: null } };
  }
  const prev = await supabase.rpc('request_brief_preview', { p_brief: brief });
  if (prev.error) return fail(prev.error.message, prev.error.code);
  return { ok: true, data: { key: built.spec.key, previewId: prev.data as string } };
}

/** Reads the preview the worker works out (pending until it is ready). */
export async function loadColumnPreview(ws: string, brief: string, preview: string): Promise<Result<ColumnPreview>> {
  if (![ws, brief, preview].every((x) => UUID.test(x))) return { ok: false, error: 'That preview was not found.' };
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const { data, error } = await supabase
    .from('brief_preview')
    .select('id, status, tier, estimate_usd, duration_minutes, over_budget, result')
    .eq('id', preview)
    .eq('brief_id', brief)
    .eq('workspace_id', ws)
    .maybeSingle();
  if (error || !data) return fail(error?.message ?? 'That preview was not found');
  const p = data as {
    id: string;
    status: ColumnPreview['status'];
    tier: string | null;
    estimate_usd: number | null;
    duration_minutes: number | null;
    over_budget: boolean | null;
    result: { counts?: { reextract_records?: number } } | null;
  };
  let approval: string | null = null;
  if (p.over_budget) {
    const { data: rows } = await supabase
      .from('budget_approval')
      .select('status')
      .eq('job_type', 'brief.publish')
      .eq('job_args->>preview_id', p.id)
      .order('requested_at', { ascending: false })
      .limit(1);
    approval = (rows?.[0]?.status as string | undefined) ?? null;
  }
  return {
    ok: true,
    data: {
      id: p.id,
      status: p.status,
      tier: p.tier,
      estimate_usd: Number(p.estimate_usd ?? 0),
      duration_minutes: p.duration_minutes,
      over_budget: !!p.over_budget,
      records: Number(p.result?.counts?.reextract_records ?? 0),
      approval,
    },
  };
}

/** An over-budget preview waits for an Owner or Admin (S6). */
export async function requestColumnApproval(preview: string): Promise<Result> {
  if (!UUID.test(preview)) return { ok: false, error: 'That preview was not found.' };
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const { error } = await supabase.rpc('request_publish_approval', { p_preview: preview });
  if (error) return fail(error.message, error.code);
  return { ok: true, data: undefined };
}

/**
 * Add column, step 4: publishes through the existing path (publish_brief needs the matching preview).
 * R9: only when the draft is still the published version plus exactly this one column; anything
 * else (someone edited the Brief since the preview) is refused (55000), not published.
 */
export async function publishColumn(
  ws: string,
  brief: string,
  key: string,
  label: string,
): Promise<Result<{ predicateId: string | null; fillMethod: string | null; queued: number }>> {
  if (!UUID.test(ws) || !UUID.test(brief)) return { ok: false, error: 'That Brief was not found.' };
  const { supabase, brief: b, published } = await loadBrief(ws, brief);
  if (!supabase) return NOT_CONFIGURED;
  if (!b) return { ok: false, error: 'That Brief was not found.' };
  const draft = b.draft_config;
  const added = draft?.predicates?.filter((p) => isColumnSpec(p) && p.key === key) ?? [];
  const expected: BriefConfig | null =
    published && added.length === 1 ? { ...published, predicates: [...(published.predicates ?? []), added[0]] } : null;
  if (!draft || !expected || !sameConfig(draft, expected)) return pendingRefusal;
  const { error } = await supabase.rpc('publish_brief', { p_brief: brief, p_change_note: `Added column "${label.slice(0, 80)}"` });
  if (error) return fail(error.message, error.code);
  const { data } = await supabase.from('predicate').select('id, fill_method').eq('workspace_id', ws).eq('key', key).maybeSingle();
  const p = data as { id: string; fill_method: string } | null;
  // Publishing a formula column queues it for every account whose cell is empty (P3 backfill).
  let queued = 0;
  if (p?.fill_method === 'formula') {
    const { count } = await supabase
      .from('predicate_run')
      .select('id', { count: 'exact', head: true })
      .eq('predicate_id', p.id)
      .eq('workspace_id', ws)
      .in('status', ['queued', 'running']);
    queued = count ?? 0;
  }
  refresh();
  return { ok: true, data: { predicateId: p?.id ?? null, fillMethod: p?.fill_method ?? null, queued } };
}

export type RunTarget = {
  predicate: string;
  mode: RunMode;
  /** The lens Brief, or null under All Briefs. */
  brief: string | null;
  /** The view's filter AST (compiled by the server through app.filter_where), or null. */
  filter: Record<string, unknown> | null;
  /** Mode selected: the selected rows' entities. */
  entityIds?: string[];
};

function runArgs(ws: string, t: RunTarget): Record<string, unknown> | null {
  if (!UUID.test(ws) || !UUID.test(t.predicate) || (t.brief && !UUID.test(t.brief))) return null;
  if (!MODES.includes(t.mode)) return null;
  const ids = t.mode === 'selected' ? (t.entityIds ?? []) : null;
  if (ids && (ids.length === 0 || ids.length > MAX_SELECTED || !ids.every((x) => UUID.test(x)))) return null;
  const filter = t.filter && typeof t.filter === 'object' && !Array.isArray(t.filter) && Object.keys(t.filter).length ? t.filter : null;
  return { p_ws: ws, p_predicate: t.predicate, p_mode: t.mode, p_brief: t.brief, p_filter: filter, p_entity_ids: ids };
}

/** What a run would queue and cost. Reads only (Viewer+). */
export async function estimateRun(ws: string, t: RunTarget): Promise<Result<RunEstimate>> {
  const args = runArgs(ws, t);
  if (!args) return { ok: false, error: 'That run was not understood.' };
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const { data, error } = await supabase.rpc('estimate_column_run', args);
  if (error || !data) return fail(error?.message, error?.code);
  const e = data as Record<string, unknown>;
  const num = (v: unknown) => Number(v ?? 0);
  return {
    ok: true,
    data: {
      count: num(e.count),
      per_entity_usd: num(e.per_entity_usd),
      cost_usd: num(e.cost_usd),
      remaining_usd: e.remaining_usd == null ? null : num(e.remaining_usd),
      within_budget: num(e.within_budget),
      max_per_request: num(e.max_per_request),
    },
  };
}

/**
 * Queues the runs, after the person saw the estimate. A budget refusal (55000) keeps the server's
 * message and names the N that fits (`firstN`), so the form can offer "Run the first N".
 */
export async function requestRun(ws: string, t: RunTarget, limit?: number): Promise<Result<{ queued: number; cost_usd: number }>> {
  const args = runArgs(ws, t);
  if (!args) return { ok: false, error: 'That run was not understood.' };
  if (limit !== undefined && (!Number.isInteger(limit) || limit < 1 || limit > MAX_SELECTED)) return { ok: false, error: 'That limit was not understood.' };
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const { data, error } = await supabase.rpc('request_column_run', { ...args, p_limit: limit ?? null });
  if (error) {
    const m = /run the first (\d+) within budget/.exec(error.message);
    return { ...fail(error.message, error.code), firstN: error.code === '55000' && m ? Number(m[1]) : undefined };
  }
  refresh();
  const r = (data ?? {}) as { queued?: number; cost_usd?: number };
  return { ok: true, data: { queued: Number(r.queued ?? 0), cost_usd: Number(r.cost_usd ?? 0) } };
}

/** Pauses (false) or resumes (true) a formula column's auto-run. */
export async function setAutoRun(ws: string, predicate: string, on: boolean): Promise<Result> {
  if (!UUID.test(ws) || !UUID.test(predicate) || typeof on !== 'boolean') return { ok: false, error: 'That column was not found.' };
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const { error } = await supabase.rpc('set_auto_run', { p_ws: ws, p_predicate: predicate, p_on: on });
  if (error) return fail(error.message, error.code);
  refresh();
  return { ok: true, data: undefined };
}
