'use server';
import { revalidatePath } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { friendlyError } from '@/lib/org';

const text = (form: FormData, key: string) => String(form.get(key) ?? '').trim();

export async function saveSegment(form: FormData): Promise<void> {
  const ws = text(form, 'workspace');
  const name = text(form, 'name');
  if (!ws || !name) return;
  let filter: unknown = {};
  try {
    filter = JSON.parse(text(form, 'filter') || '{}');
  } catch {
    filter = {};
  }
  const supabase = await createClient();
  if (!supabase) return;
  const { error } = await supabase.rpc('save_segment', { p_ws: ws, p_id: null, p_name: name, p_filter: filter });
  if (error) {
    console.error(friendlyError(error.message));
    return;
  }
  revalidatePath('/settings');
}