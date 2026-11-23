'use server';
import { revalidatePath } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { friendlyError } from '@/lib/org';

const text = (form: FormData, key: string) => String(form.get(key) ?? '').trim();

export async function listColumnTemplates(ws: string) {
  const supabase = await createClient();
  if (!supabase) return [];
  const { data } = await supabase.rpc('list_column_templates', { p_ws: ws });
  return (data as unknown[]) ?? [];
}

export async function loadColumnTemplates(ws: string): Promise<Array<{ id: string; name: string; spec: unknown }>> {
  const data = await listColumnTemplates(ws);
  return data as Array<{ id: string; name: string; spec: unknown }>;
}

export async function saveColumnTemplate(form: FormData) {
  const ws = text(form, 'workspace');
  const name = text(form, 'name');
  const specRaw = text(form, 'spec');
  if (!ws || !name || !specRaw) return;
  let spec: unknown;
  try {
    spec = JSON.parse(specRaw);
  } catch {
    return;
  }
  const supabase = await createClient();
  if (!supabase) return;
  const { error } = await supabase.rpc('save_column_template', { p_ws: ws, p_name: name, p_spec: spec });
  if (error) console.error(friendlyError(error.message));
  revalidatePath('/settings');
}

export async function deleteColumnTemplate(form: FormData) {
  const id = text(form, 'template');
  if (!id) return;
  const supabase = await createClient();
  if (!supabase) return;
  const { error } = await supabase.rpc('delete_column_template', { p_template: id });
  if (error) console.error(friendlyError(error.message));
  revalidatePath('/settings');
}