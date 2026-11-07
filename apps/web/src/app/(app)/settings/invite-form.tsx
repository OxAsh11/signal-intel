'use client';

import { useActionState, useState } from 'react';
import { Button } from '@signal/ui';
import { inviteMember, type ActionState } from '@/app/actions/access';
import { inputClass } from '@/components/forms';

const ORG_ROLES = ['Owner', 'Admin'];

export function InviteForm({
  orgId,
  workspaces,
  canInviteOwner,
}: {
  orgId: string;
  workspaces: { id: string; name: string }[];
  canInviteOwner: boolean;
}) {
  const [state, action, pending] = useActionState<ActionState, FormData>(inviteMember, {});
  const [role, setRole] = useState('Editor');
  const [copied, setCopied] = useState(false);
  const roles = [...(canInviteOwner ? ['Owner'] : []), 'Admin', 'Editor', 'Operator', 'Viewer'];

  return (
    <div className="flex flex-col gap-3">
      <form action={action} className="flex flex-wrap items-end gap-3">
        <input type="hidden" name="org" value={orgId} />
        <label className="flex min-w-[220px] flex-1 flex-col gap-1">
          <span className="si-label">Email</span>
          <input name="email" type="email" required className={inputClass} placeholder="name@company.com" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="si-label">Role</span>
          <select name="role" value={role} onChange={(e) => setRole(e.target.value)} className={inputClass}>
            {roles.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </label>
        {!ORG_ROLES.includes(role) ? (
          <label className="flex flex-col gap-1">
            <span className="si-label">Workspace</span>
            <select name="workspace" className={inputClass}>
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <Button type="submit" variant="primary" disabled={pending}>
          {pending ? 'Inviting…' : 'Invite'}
        </Button>
      </form>
      {state.error ? (
        <p role="alert" className="m-0 text-[13px] text-danger">
          {state.error}
        </p>
      ) : null}
      {state.inviteLink ? (
        <div className="flex flex-col gap-2 rounded-md border border-line bg-surface-sunken p-3">
          <span className="text-[13px]">
            Invitation created. Send this link to them. It works only for that email address, expires in 7 days, and
            is shown only once.
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 overflow-x-auto rounded-sm bg-surface px-2 py-1 font-mono text-[12px] whitespace-nowrap">
              {state.inviteLink}
            </code>
            <Button
              size="sm"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(state.inviteLink!);
                  setCopied(true);
                } catch {
                  setCopied(false);
                }
              }}
            >
              {copied ? 'Copied' : 'Copy link'}
            </Button>
          </div>
          <span className="text-[12px] text-ink-muted">
            They can also sign in with that email and accept from the Get started page.
          </span>
        </div>
      ) : null}
    </div>
  );
}
