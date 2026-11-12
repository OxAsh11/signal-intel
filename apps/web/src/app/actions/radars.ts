'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';
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

/**
 * Create a new radar attached to a brief.
 * Calls the SECURITY DEFINER RPC `public.create_radar`.
 * Hidden fields required: `brief`, `workspace`, `connector_type`, `name`.
 * Optional: `config` (JSON string).
 */
export async function createRadar(_: ActionState, form: FormData): Promise<ActionState> {
  const brief = text(form, 'brief');
  const workspace = text(form, 'workspace');
  const connectorType = text(form, 'connector_type');
  const name = text(form, 'name');

  if (!brief) return { error: 'Missing brief ID.' };
  if (!workspace) return { error: 'Missing workspace ID.' };
  if (!connectorType) return { error: 'Missing connector type.' };
  if (!name) return { error: 'Enter a name for the radar.' };

  let config: unknown = {};
  const configRaw = text(form, 'config');
  if (configRaw) {
    try {
      config = JSON.parse(configRaw);
    } catch {
      return { error: 'Invalid config JSON.' };
    }
  }

  const { data, error } = await rpc('create_radar', {
    p_ws: workspace,
    p_brief: brief,
    p_connector_type: connectorType,
    p_name: name,
    p_config: config,
  });
  if (error) return { error };
  revalidatePath(`/briefs/${brief}/radars`);
  return { ok: true, radarId: data as string } as ActionState & { radarId: string };
}

/**
 * Remove a radar.
 * Calls the SECURITY DEFINER RPC `public.remove_radar`.
 * Required field: `id` (radar UUID).
 */
export async function removeRadar(_: ActionState, form: FormData): Promise<ActionState> {
  const id = text(form, 'id');
  if (!id) return { error: 'Missing radar ID.' };

  const { error } = await rpc('remove_radar', { p_radar: id });
  if (error) return { error };
  revalidatePath('/briefs');
  return { ok: true };
}

/**
 * Upload a CSV file to Supabase Storage and create a csv_upload radar for it.
 * Expects FormData with: `brief`, `workspace`, `name`, `file` (File).
 * Uploads to `uploads/{workspace}/{timestamp}_{filename}` before creating the radar,
 * then passes storage_path in the radar config so the worker can find the file immediately.
 */
export async function uploadCsvRadar(_: ActionState, form: FormData): Promise<ActionState> {
  const brief = text(form, 'brief');
  const workspace = text(form, 'workspace');
  const name = text(form, 'name');
  const file = form.get('file');

  if (!brief) return { error: 'Missing brief ID.' };
  if (!workspace) return { error: 'Missing workspace ID.' };
  if (!name) return { error: 'Enter a name for the radar.' };
  if (!(file instanceof File) || file.size === 0) return { error: 'Select a CSV file to upload.' };

  // Step 1: Upload the CSV to Supabase Storage first (before creating the radar).
  const supabase = await createClient();
  if (!supabase) return { error: 'Supabase is not configured.' };

  const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, '_');
  const storagePath = `uploads/${workspace}/${Date.now()}_${safeName}`;
  const fileBuffer = await file.arrayBuffer();
  const { error: uploadError } = await supabase.storage
    .from('signal-uploads')
    .upload(storagePath, fileBuffer, { contentType: 'text/csv', upsert: false });

  if (uploadError) return { error: friendlyError(uploadError.message) };

  // Step 2: Create the radar with the storage path already in config so the
  // worker can find the file immediately when radar.fetch_queued fires.
  const { data: radarId, error: createError } = await rpc('create_radar', {
    p_ws: workspace,
    p_brief: brief,
    p_connector_type: 'csv_upload',
    p_name: name,
    p_config: { storage_path: storagePath },
  });
  if (createError) return { error: createError };

  revalidatePath(`/briefs/${brief}`);
  redirect(`/briefs/${brief}`);
}

/**
 * Form-compatible wrapper around uploadCsvRadar for direct use as a form `action`.
 * Returns void so it satisfies the HTML form action type constraint.
 */
export async function csvRadarFormAction(form: FormData): Promise<void> {
  await uploadCsvRadar({}, form);
}
