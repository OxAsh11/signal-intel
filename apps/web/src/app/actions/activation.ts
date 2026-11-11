'use server';

import { revalidatePath } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { friendlyError } from '@/lib/org';

/** Every write goes through an audited SQL function; the database re-checks the caller's role. */
async function call(fn: string, args: Record<string, unknown>, path: string): Promise<void> {
  const supabase = await createClient();
  if (!supabase) throw new Error('Supabase is not configured.');
  const { error } = await supabase.rpc(fn, args);
  if (error) throw new Error(friendlyError(error.message));
  revalidatePath(path);
}

const text = (form: FormData, key: string) => String(form.get(key) ?? '').trim();

// ── API tokens (M13) ──────────────────────────────────────────────────────────

export type TokenState = { token?: string; error?: string };

/** The token is generated in the database, stored only as a hash and returned once. */
export async function createApiToken(_: TokenState, form: FormData): Promise<TokenState> {
  const name = text(form, 'name');
  if (!name) return { error: 'Enter a name for the token.' };
  const supabase = await createClient();
  if (!supabase) return { error: 'Supabase is not configured.' };
  const { data, error } = await supabase.rpc('create_api_token', {
    p_workspace_id: text(form, 'workspace'),
    p_name: name,
    p_scope: text(form, 'scope') || 'Read',
  });
  if (error) return { error: friendlyError(error.message) };
  revalidatePath('/settings');
  return { token: String(data) };
}

export async function revokeApiToken(tokenId: string): Promise<void> {
  await call('revoke_api_token', { p_token_id: tokenId }, '/settings');
}

// ── Inbox (M10) ───────────────────────────────────────────────────────────────

/** A human mark always wins and is stored as feedback. */
export async function classifyReply(activityId: string, wsid: string, form: FormData): Promise<void> {
  const replyClass = text(form, 'reply_class');
  if (!replyClass) throw new Error('Choose a reply class.');
  await call(
    'classify_reply',
    { p_activity_id: activityId, p_reply_class: replyClass },
    `/workspace/${wsid}/inbox`,
  );
}

// ── Campaigns (M9) ────────────────────────────────────────────────────────────

export async function createCampaign(wsid: string, form: FormData): Promise<void> {
  const name = text(form, 'name');
  const brief = text(form, 'brief');
  if (!name) throw new Error('Enter a name for the campaign.');
  if (!brief) throw new Error('Choose a Brief.');
  await call(
    'create_campaign',
    { p_workspace_id: wsid, p_brief_id: brief, p_name: name, p_motion: text(form, 'motion') },
    `/workspace/${wsid}/campaigns`,
  );
}

/** off → dry run → live. The database refuses live unless the campaign is in dry run. */
export async function setCampaignPushMode(campaignId: string, wsid: string, mode: string): Promise<void> {
  await call(
    'set_campaign_push_mode',
    { p_campaign_id: campaignId, p_push_mode: mode },
    `/workspace/${wsid}/campaigns`,
  );
}

export async function setCampaignStatus(campaignId: string, wsid: string, status: string): Promise<void> {
  await call(
    'set_campaign_status',
    { p_campaign_id: campaignId, p_status: status },
    `/workspace/${wsid}/campaigns`,
  );
}

// ── Meetings and tasks (M12) ──────────────────────────────────────────────────

/** An explicit human confirmation is one of the two accepted proofs that a meeting was held. */
export async function confirmMeeting(meetingId: string, wsid: string): Promise<void> {
  await call('confirm_meeting', { p_meeting_id: meetingId }, `/workspace/${wsid}/meetings`);
}

export async function completeTask(taskId: string, wsid: string): Promise<void> {
  await call('complete_task', { p_task_id: taskId }, `/workspace/${wsid}/meetings`);
}
