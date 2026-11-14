import type { Role } from '@signal/contracts';
import { Button } from '@signal/ui';
import { removeMember, revokeInvitation, setAllowedDomains, setMemberRole } from '@/app/actions/access';
import { ActionForm, inputClass } from '@/components/forms';
import { atLeast, getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';
import { InviteForm } from './invite-form';

type MemberRow = {
  id: string;
  email: string;
  display_name: string | null;
  role_assignment: { role: Role; workspace_id: string | null }[];
};
type InvitationRow = { id: string; email: string; role: Role; workspace_id: string | null; expires_at: string };

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="si-card">
      <div className="flex flex-col gap-0.5">
        <h2 className="si-h2">{title}</h2>
        {hint ? <p className="m-0 max-w-[70ch] text-[13px] text-ink-muted">{hint}</p> : null}
      </div>
      {children}
    </section>
  );
}

export async function MembersAndAccess() {
  const ctx = await getOrgContext();
  const supabase = await createClient();
  if (!ctx?.org || !supabase) return null;
  const org = ctx.org;
  const isAdmin = atLeast(ctx.orgRole, 'Admin');
  const isOwner = ctx.orgRole === 'Owner';
  const workspaces = ctx.workspaces.filter((w) => w.orgId === org.id);
  const wsName = (id: string | null) => workspaces.find((w) => w.id === id)?.name ?? 'Unknown workspace';

  const [{ data: memberData }, { data: inviteData }] = await Promise.all([
    supabase
      .from('member')
      .select('id, email, display_name, role_assignment(role, workspace_id)')
      .eq('org_id', org.id)
      .eq('status', 'active')
      .order('created_at'),
    isAdmin
      ? supabase
          .from('invitation')
          .select('id, email, role, workspace_id, expires_at')
          .eq('org_id', org.id)
          .is('accepted_at', null)
          .is('revoked_at', null)
          .gt('expires_at', new Date().toISOString())
          .order('created_at')
      : Promise.resolve({ data: [] }),
  ]);
  const members = (memberData ?? []) as unknown as MemberRow[];
  const invitations = (inviteData ?? []) as InvitationRow[];

  return (
    <>
      <Section
        title="Members"
        hint={
          isAdmin
            ? 'Owner and Admin apply to the whole organisation. Editor, Operator and Viewer are set per workspace. Every change is recorded in the audit log.'
            : 'Only Owners and Admins can change members and roles.'
        }
      >
        <div className="overflow-x-auto">
          <table className="si-table">
            <thead>
              <tr className="border-b border-line">
                <th className="si-label px-2 py-2 font-semibold">Member</th>
                <th className="si-label px-2 py-2 font-semibold">Organisation role</th>
                <th className="si-label px-2 py-2 font-semibold">Workspace roles</th>
                {isAdmin ? <th className="si-label px-2 py-2 font-semibold">Change</th> : null}
              </tr>
            </thead>
            <tbody>
              {members.map((m) => {
                const orgRole = m.role_assignment.find((r) => r.workspace_id === null)?.role;
                const wsRoles = m.role_assignment.filter((r) => r.workspace_id !== null);
                const canTouch = isOwner || orgRole !== 'Owner';
                return (
                  <tr key={m.id} className="border-b border-line align-top last:border-b-0">
                    <td className="px-2 py-2">
                      <div className="font-semibold">{m.display_name ?? m.email}</div>
                      {m.display_name ? <div className="text-[12px] text-ink-muted">{m.email}</div> : null}
                      {m.id === ctx.memberId ? <div className="text-[12px] text-ink-muted">You</div> : null}
                    </td>
                    <td className="px-2 py-2">{orgRole ? <span className="si-chip si-band">{orgRole}</span> : <span className="text-ink-faint">None</span>}</td>
                    <td className="px-2 py-2">
                      <div className="flex flex-wrap gap-1">
                        {wsRoles.length === 0 ? <span className="text-ink-faint">None</span> : null}
                        {wsRoles.map((r) => (
                          <span key={r.workspace_id} className="si-chip si-band">
                            {wsName(r.workspace_id)}: {r.role}
                          </span>
                        ))}
                      </div>
                    </td>
                    {isAdmin ? (
                      <td className="px-2 py-2">
                        {canTouch ? (
                          <div className="flex flex-col gap-2">
                            <ActionForm action={setMemberRole} className="flex flex-wrap items-center gap-2">
                              <input type="hidden" name="member" value={m.id} />
                              <select name="role" aria-label={`Role for ${m.email}`} className={inputClass} defaultValue="Editor">
                                {isOwner ? <option value="Owner">Owner (organisation)</option> : null}
                                <option value="Admin">Admin (organisation)</option>
                                <option value="Editor">Editor</option>
                                <option value="Operator">Operator</option>
                                <option value="Viewer">Viewer</option>
                                <option value="none">Remove role</option>
                              </select>
                              <select name="workspace" aria-label="Workspace" className={inputClass} defaultValue={workspaces[0]?.id}>
                                <option value="">Whole organisation</option>
                                {workspaces.map((w) => (
                                  <option key={w.id} value={w.id}>
                                    {w.name}
                                  </option>
                                ))}
                              </select>
                              <Button type="submit" size="sm">
                                Apply
                              </Button>
                            </ActionForm>
                            <ActionForm action={removeMember}>
                              <input type="hidden" name="member" value={m.id} />
                              <Button type="submit" size="sm" variant="quiet">
                                Remove from organisation
                              </Button>
                            </ActionForm>
                          </div>
                        ) : (
                          <span className="text-[12px] text-ink-muted">Only an Owner can change an Owner</span>
                        )}
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Section>

      {isAdmin ? (
        <Section title="Invite people" hint="Invitations work only for the email address you enter, and expire after 7 days.">
          <InviteForm orgId={org.id} workspaces={workspaces.map(({ id, name }) => ({ id, name }))} canInviteOwner={isOwner} />
          {invitations.length > 0 ? (
            <div className="flex flex-col gap-2">
              <span className="si-label">Pending invitations</span>
              {invitations.map((inv) => (
                <ActionForm key={inv.id} action={revokeInvitation} className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2">
                  <input type="hidden" name="invitation" value={inv.id} />
                  <span>
                    {inv.email}
                    <span className="text-ink-muted">
                      {' '}
                      · {inv.role}
                      {inv.workspace_id ? ` in ${wsName(inv.workspace_id)}` : ''} · expires{' '}
                      {new Date(inv.expires_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
                    </span>
                  </span>
                  <Button type="submit" size="sm" variant="quiet">
                    Revoke
                  </Button>
                </ActionForm>
              ))}
            </div>
          ) : null}
        </Section>
      ) : null}

      <Section
        title="Allowed sign-in domains"
        hint="When set, only people with these email domains can accept invitations to this organisation. Leave empty to allow any domain."
      >
        {isAdmin ? (
          <ActionForm action={setAllowedDomains} className="flex flex-wrap items-end gap-3" successText="Saved.">
            <input type="hidden" name="org" value={org.id} />
            <label className="flex min-w-[260px] flex-1 flex-col gap-1">
              <span className="si-label">Domains</span>
              <input name="domains" className={inputClass} defaultValue={org.allowedDomains.join(', ')} placeholder="company.com, company.co.uk" />
            </label>
            <Button type="submit">Save domains</Button>
          </ActionForm>
        ) : (
          <p className="m-0">{org.allowedDomains.length ? org.allowedDomains.join(', ') : 'Any domain'}</p>
        )}
      </Section>
    </>
  );
}
