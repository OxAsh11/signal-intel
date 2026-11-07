'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { refresh } from 'next/cache';
import type { Role } from '@signal/contracts';
import { createClient } from '@/lib/supabase/server';
import { WORKSPACE_COOKIE, friendlyError } from '@/lib/org';

export type ActionState = { ok?: boolean; error?: string; inviteLink?: string };

/** Every write goes through an audited SQL function; the database re-checks the caller's role. */
async function rpc(fn: string, args: Record<string, unknown>) {
  const supabase = await createClient();
  if (!supabase) return { data: null, error: 'Supabase is not configured.' };
  const { data, error } = await supabase.rpc(fn, args);
  return { data, error: error ? friendlyError(error.message) : null };
}

const text = (form: FormData, key: string) => String(form.get(key) ?? '').trim();
const optionalId = (form: FormData, key: string) => text(form, key) || null;

export async function createOrganisation(_: ActionState, form: FormData): Promise<ActionState> {
  const name = text(form, 'name');
  const workspace = text(form, 'workspace') || 'Default';
  if (!name) return { error: 'Enter your organisation name.' };
  const { error } = await rpc('create_organisation', { p_name: name, p_workspace_name: workspace });
  if (error) return { error };
  (await cookies()).delete(WORKSPACE_COOKIE);
  redirect('/');
}

export async function acceptMyInvitation(_: ActionState, form: FormData): Promise<ActionState> {
  const { error } = await rpc('accept_my_invitation', { p_invitation: text(form, 'invitation') });
  if (error) return { error };
  redirect('/');
}

export async function acceptInvitationToken(_: ActionState, form: FormData): Promise<ActionState> {
  const { error } = await rpc('accept_invitation', { p_token: text(form, 'token') });
  if (error) return { error };
  redirect('/');
}

export async function inviteMember(_: ActionState, form: FormData): Promise<ActionState> {
  const role = text(form, 'role') as Role;
  const orgLevel = role === 'Owner' || role === 'Admin';
  const { data, error } = await rpc('invite_member', {
    p_org: text(form, 'org'),
    p_email: text(form, 'email'),
    p_role: role,
    p_workspace: orgLevel ? null : optionalId(form, 'workspace'),
  });
  if (error) return { error };
  refresh();
  const site = process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000';
  return { ok: true, inviteLink: `${site}/invite/${String(data)}` };
}

export async function revokeInvitation(_: ActionState, form: FormData): Promise<ActionState> {
  const { error } = await rpc('revoke_invitation', { p_invitation: text(form, 'invitation') });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function setMemberRole(_: ActionState, form: FormData): Promise<ActionState> {
  const role = text(form, 'role') as Role | 'none';
  const member = text(form, 'member');
  const workspace = optionalId(form, 'workspace');
  const { error } =
    role === 'none'
      ? await rpc('remove_role', { p_member: member, p_workspace: workspace })
      : await rpc('set_member_role', { p_member: member, p_role: role, p_workspace: workspace });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function removeMember(_: ActionState, form: FormData): Promise<ActionState> {
  const { error } = await rpc('remove_member', { p_member: text(form, 'member') });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function setAllowedDomains(_: ActionState, form: FormData): Promise<ActionState> {
  const domains = text(form, 'domains')
    .split(/[\s,]+/)
    .filter(Boolean);
  const { error } = await rpc('set_allowed_domains', { p_org: text(form, 'org'), p_domains: domains });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function switchWorkspace(form: FormData): Promise<void> {
  const id = text(form, 'workspace');
  if (id) (await cookies()).set(WORKSPACE_COOKIE, id, { httpOnly: true, sameSite: 'lax', path: '/', maxAge: 60 * 60 * 24 * 365 });
  refresh();
}
