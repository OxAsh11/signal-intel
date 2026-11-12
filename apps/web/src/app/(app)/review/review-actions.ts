'use server';

import { revalidatePath } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { friendlyError } from '@/lib/org';

/** Every decision goes through an audited SQL function; the database re-checks the caller's role. */
async function call(fn: string, args: Record<string, unknown>): Promise<void> {
  const supabase = await createClient();
  if (!supabase) throw new Error('Supabase is not configured.');
  const { error } = await supabase.rpc(fn, args);
  if (error) throw new Error(friendlyError(error.message));
  revalidatePath('/review');
  revalidatePath('/');
}

const text = (form: FormData, key: string) => String(form.get(key) ?? '').trim();

// ── Identity matches (M4) ─────────────────────────────────────────────────────

export async function mergeAction(itemId: string): Promise<void> {
  await call('resolve_review_item', { p_id: itemId, p_action: 'merge' });
}

export async function skipAction(itemId: string): Promise<void> {
  await call('resolve_review_item', { p_id: itemId, p_action: 'skip' });
}

export async function createNewAction(itemId: string, form: FormData): Promise<void> {
  const newName = text(form, 'new_name');
  if (!newName) throw new Error('Enter a name for the new entity.');
  await call('resolve_review_item', { p_id: itemId, p_action: 'create_new', p_new_entity_name: newName });
}

// ── Verdicts (M5) ─────────────────────────────────────────────────────────────

/** "keep" confirms the verdict as shown; any other value corrects it. The mark is stored as feedback. */
export async function resolveVerdictReview(reviewId: string, form: FormData): Promise<void> {
  const value = text(form, 'value');
  if (!value) throw new Error('Choose a verdict.');
  await call('resolve_verdict_review', {
    p_id: reviewId,
    p_action: value === 'keep' ? 'confirm' : 'correct',
    p_value: value === 'keep' ? null : value,
    p_reason: text(form, 'reason') || null,
  });
}

// ── Replies (M10) ─────────────────────────────────────────────────────────────

export async function classifyOverdueReply(activityId: string, form: FormData): Promise<void> {
  const replyClass = text(form, 'reply_class');
  if (!replyClass) throw new Error('Choose a reply class.');
  await call('classify_reply', { p_activity_id: activityId, p_reply_class: replyClass });
}

// ── Unmatched meetings (M12) ──────────────────────────────────────────────────

export async function matchMeeting(meetingId: string, form: FormData): Promise<void> {
  const account = text(form, 'account');
  if (!account) throw new Error('Choose an account.');
  await call('match_meeting', { p_meeting_id: meetingId, p_account_id: account });
}
