'use server';
import { revalidatePath } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { friendlyError } from '@/lib/org';

const text = (form: FormData, key: string) => String(form.get(key) ?? '').trim();

export async function generateBusinessContext(form: FormData): Promise<void> {
  const ws = text(form, 'workspace');
  const domain = text(form, 'domain');
  if (!ws || !domain) return;
  const supabase = await createClient();
  if (!supabase) return;
  const { error } = await supabase.rpc('generate_business_context', { p_ws: ws, p_domain: domain });
  if (error) {
    console.error(friendlyError(error.message));
    return;
  }
  revalidatePath('/settings');
}

export async function setBusinessContext(form: FormData): Promise<void> {
  const ws = text(form, 'workspace');
  const context = text(form, 'context');
  const supabase = await createClient();
  if (!supabase) return;
  const lines = context.split('\n').map((l) => l.trim()).filter(Boolean).slice(0, 5);
  const { error } = await supabase.rpc('set_business_context', { p_ws: ws, p_context: { lines } });
  if (error) {
    console.error(friendlyError(error.message));
    return;
  }
  revalidatePath('/settings');
}