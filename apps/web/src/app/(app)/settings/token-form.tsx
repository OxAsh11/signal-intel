'use client';

import { useActionState } from 'react';
import { Button } from '@signal/ui';
import { createApiToken, type TokenState } from '@/app/actions/activation';
import { inputClass } from '@/components/forms';

export function TokenForm({ workspaceId, scopes }: { workspaceId: string; scopes: string[] }) {
  const [state, action, pending] = useActionState<TokenState, FormData>(createApiToken, {});

  return (
    <div className="flex flex-col gap-3">
      <form action={action} className="flex flex-wrap items-end gap-3">
        <input type="hidden" name="workspace" value={workspaceId} />
        <label className="flex min-w-[220px] flex-1 flex-col gap-1">
          <span className="si-label">Name</span>
          <input name="name" required className={inputClass} placeholder="What will use this token" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="si-label">Scope</span>
          <select name="scope" className={inputClass} defaultValue={scopes[0]}>
            {scopes.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <Button type="submit" variant="primary" size="sm" disabled={pending}>
          Create token
        </Button>
      </form>
      {state.error ? <p className="m-0 text-[13px] text-danger">{state.error}</p> : null}
      {state.token ? (
        <div className="flex flex-col gap-1 rounded-md border border-line-strong p-3">
          <span className="si-label">Copy this token now. It is not shown again.</span>
          <code className="break-all">{state.token}</code>
        </div>
      ) : null}
    </div>
  );
}
