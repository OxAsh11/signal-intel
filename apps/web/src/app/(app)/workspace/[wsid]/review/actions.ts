'use server';

import { revalidatePath } from 'next/cache';
import { createClient } from '@/lib/supabase/server';

export async function mergeAction(itemId: string, wsid: string): Promise<void> {
  const supabase = await createClient();
  if (!supabase) throw new Error('not authenticated');
  const { error } = await supabase.rpc('resolve_review_item', {
    p_id: itemId,
    p_action: 'merge',
  });
  if (error) throw new Error(error.message);
  revalidatePath(`/workspace/${wsid}/review`);
}

export async function skipAction(itemId: string, wsid: string): Promise<void> {
  const supabase = await createClient();
  if (!supabase) throw new Error('not authenticated');
  const { error } = await supabase.rpc('resolve_review_item', {
    p_id: itemId,
    p_action: 'skip',
  });
  if (error) throw new Error(error.message);
  revalidatePath(`/workspace/${wsid}/review`);
}

/**
 * Create a new entity from a pending review item.
 * `itemId` and `wsid` are bound before passing as a form action;
 * `formData` carries the user-supplied `new_name` field.
 */
export async function createNewAction(
  itemId: string,
  wsid: string,
  formData: FormData
): Promise<void> {
  const newName = String(formData.get('new_name') ?? '').trim();
  if (!newName) throw new Error('Enter a name for the new entity.');
  const supabase = await createClient();
  if (!supabase) throw new Error('not authenticated');
  const { error } = await supabase.rpc('resolve_review_item', {
    p_id: itemId,
    p_action: 'create_new',
    p_new_entity_name: newName,
  });
  if (error) throw new Error(error.message);
  revalidatePath(`/workspace/${wsid}/review`);
}
