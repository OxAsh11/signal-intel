import 'server-only';
import { cache } from 'react';
import { cookies } from 'next/headers';
import type { Role } from '@signal/contracts';
import { createClient, getCurrentUser, type CurrentUser } from '@/lib/supabase/server';

export const WORKSPACE_COOKIE = 'si_ws';

const RANK: Record<Role, number> = { Viewer: 1, Operator: 2, Editor: 3, Admin: 4, Owner: 5 };

export function atLeast(role: Role | null | undefined, min: Role): boolean {
  return role != null && RANK[role] >= RANK[min];
}

export type OrgRef = { id: string; name: string; allowedDomains: string[] };
export type WorkspaceRef = { id: string; orgId: string; name: string };

export type OrgContext = {
  user: CurrentUser;
  orgs: OrgRef[];
  workspaces: WorkspaceRef[];
  org: OrgRef | null;
  workspace: WorkspaceRef | null;
  memberId: string | null;
  orgRole: Role | null;
  /** Effective role in the current workspace: the higher of the org and workspace role. */
  workspaceRole: Role | null;
};

/**
 * The signed-in user's organisations, workspaces and roles, read under RLS.
 * Reads cookies and the session, so callers render inside <Suspense>.
 */
export const getOrgContext = cache(async (): Promise<OrgContext | null> => {
  const user = await getCurrentUser();
  const supabase = await createClient();
  if (!user || !supabase) return null;

  const [members, workspaces] = await Promise.all([
    supabase
      .from('member')
      .select('id, org_id, organisation(id, name, allowed_email_domains), role_assignment(role, workspace_id)')
      .eq('user_id', user.id)
      .eq('status', 'active'),
    supabase.from('workspace').select('id, org_id, name').order('created_at'),
  ]);

  type MemberRow = {
    id: string;
    org_id: string;
    organisation: { id: string; name: string; allowed_email_domains: string[] } | null;
    role_assignment: { role: Role; workspace_id: string | null }[];
  };
  const rows = (members.data ?? []) as unknown as MemberRow[];
  const orgs: OrgRef[] = rows
    .filter((m) => m.organisation)
    .map((m) => ({ id: m.organisation!.id, name: m.organisation!.name, allowedDomains: m.organisation!.allowed_email_domains }));
  const wss: WorkspaceRef[] = (workspaces.data ?? []).map((w) => ({ id: w.id, orgId: w.org_id, name: w.name }));

  const chosen = (await cookies()).get(WORKSPACE_COOKIE)?.value;
  const workspace = wss.find((w) => w.id === chosen) ?? wss[0] ?? null;
  const org = orgs.find((o) => o.id === workspace?.orgId) ?? orgs[0] ?? null;
  const me = rows.find((m) => m.org_id === org?.id);
  const orgRole = me?.role_assignment.find((r) => r.workspace_id === null)?.role ?? null;
  const wsRole = me?.role_assignment.find((r) => r.workspace_id === workspace?.id)?.role ?? null;
  const workspaceRole = [orgRole, wsRole].filter((r): r is Role => r != null).sort((a, b) => RANK[b] - RANK[a])[0] ?? null;

  return { user, orgs, workspaces: wss, org, workspace, memberId: me?.id ?? null, orgRole, workspaceRole };
});

/** Turns a Postgres error raised by our SQL functions into a sentence for the interface. */
export function friendlyError(message: string | undefined): string {
  if (!message) return 'Something went wrong. Try again.';
  const m = message.replace(/^.*?ERROR:\s*/, '').trim();
  return m.charAt(0).toUpperCase() + m.slice(1) + (/[.!?]$/.test(m) ? '' : '.');
}
