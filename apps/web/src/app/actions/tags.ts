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

export async function tagAccounts(_: { error?: string } | undefined, form: FormData) {
  const ws = text(form, 'workspace');
  const tag = text(form, 'tag');
  const on = form.get('on') === 'on' || form.get('on') === 'true';
  const ids = form.getAll('account').map((v) => String(v));
  if (!ws || !tag || !ids.length) return { error: 'Missing data.' };
  const { error } = await rpc('tag_accounts', { p_ws: ws, p_tag: tag, p_account_ids: ids, p_on: on });
  if (error) return { error };
  revalidatePath('/workspace');
  return { ok: true } as { error?: string; ok?: boolean };
}