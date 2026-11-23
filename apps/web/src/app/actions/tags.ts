'use server';

import { revalidatePath } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { friendlyError } from '@/lib/org';

async function rpc(fn: string, args: Record<string, unknown>) {
  const supabase = await createClient();
  if (!supabase) return { data: null, error: 'Supabase is not configured.' };
  const { data, error } = await supabase.rpc(fn, args);
  return { data, error: error ? friendlyError(error.message) : null };
}

const text = (form: FormData, key: string) => String(form.get(key) ?? '').trim();

export async function loadWorkspaceTags(ws: string): Promise<{ id: string; name: string; colour: string }[]> {
  const supabase = await createClient();
  if (!supabase) return [];
  const { data } = await supabase.from('account_tag').select('id, name, colour').eq('workspace_id', ws).order('name');
  return (data as unknown as { id: string; name: string; colour: string }[]) ?? [];
}

export async function loadAccountTags(ws: string): Promise<Array<{ account_id: string; tag_id: string; tag_name: string }>> {
  const supabase = await createClient();
  if (!supabase) return [];
  const { data } = await supabase
    .from('account_tag_link')
    .select('account_id, tag_id, tag:tag_id (name)')
    .limit(2000);
  const rows = (data ?? []) as Array<{ account_id: string; tag_id: string; tag?: { name: string } | null }>;
  return rows
    .map((r) => ({ account_id: r.account_id, tag_id: r.tag_id, tag_name: r.tag?.name ?? '' }))
    .filter((r) => r.tag_name);
}

export async function saveTag(form: FormData) {
  const ws = text(form, 'workspace');
  const name = text(form, 'name');
  const colour = text(form, 'colour') || null;
  if (!ws || !name) return;
  await rpc('save_tag', { p_ws: ws, p_name: name, p_colour: colour });
  revalidatePath('/settings');
}

export async function deleteTag(form: FormData) {
  const tag = text(form, 'tag');
  if (!tag) return;
  await rpc('delete_tag', { p_tag: tag });
  revalidatePath('/settings');
}

export async function tagAccounts(form: FormData) {
  const ws = text(form, 'workspace');
  const tag = text(form, 'tag');
  const on = form.get('on') === 'on' || form.get('on') === 'true';
  const ids = form.getAll('account').map((v) => String(v));
  if (!ws || !tag || !ids.length) return;
  const { error } = await rpc('tag_accounts', { p_ws: ws, p_tag: tag, p_account_ids: ids, p_on: on });
  if (error) console.error(error);
  revalidatePath('/workspace');
}