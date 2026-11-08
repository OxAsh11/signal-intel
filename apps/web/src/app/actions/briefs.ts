'use server';

import { redirect } from 'next/navigation';
import { refresh } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { friendlyError } from '@/lib/org';
import type { ActionState } from './access';

/** Every write goes through an audited SQL function; the database re-checks the caller's role. */
async function rpc(fn: string, args: Record<string, unknown>) {
  const supabase = await createClient();
  if (!supabase) return { data: null, error: 'Supabase is not configured.' };
  const { data, error } = await supabase.rpc(fn, args);
  return { data, error: error ? friendlyError(error.message) : null };
}

const text = (form: FormData, key: string) => String(form.get(key) ?? '').trim();

export async function createBrief(_: ActionState, form: FormData): Promise<ActionState> {
  const name = text(form, 'name');
  const workspace = text(form, 'workspace');
  if (!name) return { error: 'Enter a name for the Brief.' };
  if (!workspace) return { error: 'Choose a workspace first.' };
  const { data, error } = await rpc('create_brief', { p_ws: workspace, p_name: name });
  if (error) return { error };
  redirect(`/briefs/${data as string}`);
}

export async function saveBriefDraft(_: ActionState, form: FormData): Promise<ActionState> {
  const brief = text(form, 'brief');
  const config = text(form, 'config');
  if (!brief || !config) return { error: 'Invalid request.' };
  let parsed: unknown;
  try {
    parsed = JSON.parse(config);
  } catch {
    return { error: 'Invalid config JSON.' };
  }
  const { error } = await rpc('save_brief_draft_ui', { p_brief: brief, p_config: parsed });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function publishBrief(_: ActionState, form: FormData): Promise<ActionState> {
  const { error } = await rpc('publish_brief', {
    p_brief: text(form, 'brief'),
    p_change_note: text(form, 'change_note') || null,
  });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function updateBriefState(_: ActionState, form: FormData): Promise<ActionState> {
  const { error } = await rpc('update_brief_state', {
    p_brief: text(form, 'brief'),
    p_new_state: text(form, 'state'),
  });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function createWorkspacePredicate(_: ActionState, form: FormData): Promise<ActionState> {
  const allowedRaw = text(form, 'allowed_values');
  let allowedValues: unknown = null;
  if (allowedRaw) {
    try {
      allowedValues = JSON.parse(allowedRaw);
    } catch {
      return { error: 'Invalid allowed_values JSON.' };
    }
  }
  const { error } = await rpc('create_workspace_predicate', {
    p_ws: text(form, 'workspace'),
    p_key: text(form, 'key'),
    p_label: text(form, 'label'),
    p_value_type: text(form, 'value_type'),
    p_allowed_values: allowedValues,
    p_extraction_hint: text(form, 'extraction_hint') || null,
  });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function addExclusion(_: ActionState, form: FormData): Promise<ActionState> {
  const valType = text(form, 'value_type');
  const { error } = await rpc('add_exclusion', {
    p_brief: text(form, 'brief'),
    p_value_type: valType,
    p_entity_id: valType === 'entity' ? (text(form, 'entity_id') || null) : null,
    p_pattern: valType !== 'entity' ? (text(form, 'pattern') || null) : null,
    p_reason: text(form, 'reason') || null,
  });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function removeExclusion(_: ActionState, form: FormData): Promise<ActionState> {
  const { error } = await rpc('remove_exclusion', { p_id: text(form, 'id') });
  if (error) return { error };
  refresh();
  return { ok: true };
}
