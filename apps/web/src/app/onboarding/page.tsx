import type { Metadata } from 'next';
import Link from 'next/link';
import { Suspense } from 'react';
import { Button } from '@signal/ui';
import { acceptMyInvitation, createOrganisation } from '@/app/actions/access';
import { ActionForm, inputClass } from '@/components/forms';
import { getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';

export const metadata: Metadata = { title: 'Get started' };

type Invitation = { id: string; org_name: string; workspace_name: string | null; role: string; expires_at: string };

async function Onboarding() {
  const ctx = await getOrgContext();
  if (!ctx) {
    return (
      <p className="m-0">
        <Link href="/login?next=/onboarding" className="text-signal">
          Sign in
        </Link>{' '}
        to continue.
      </p>
    );
  }
  const supabase = await createClient();
  const { data } = supabase ? await supabase.rpc('my_invitations') : { data: [] };
  const invitations = (data ?? []) as Invitation[];

  return (
    <div className="flex flex-col gap-6">
      {ctx.orgs.length > 0 ? (
        <p className="m-0 rounded-md border border-line bg-surface-sunken p-3">
          You already belong to {ctx.orgs.map((o) => o.name).join(', ')}.{' '}
          <Link href="/" className="text-signal">
            Go to Signal Intel
          </Link>
        </p>
      ) : null}

      {invitations.length > 0 ? (
        <section className="flex flex-col gap-3">
          <h2 className="m-0 text-[15px] leading-[22px] font-semibold">Invitations for {ctx.user.email}</h2>
          {invitations.map((inv) => (
            <ActionForm
              key={inv.id}
              action={acceptMyInvitation}
              className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-surface p-3"
            >
              <input type="hidden" name="invitation" value={inv.id} />
              <span>
                <strong>{inv.org_name}</strong>
                <span className="text-ink-muted">
                  {' '}
                  · {inv.role}
                  {inv.workspace_name ? ` in ${inv.workspace_name}` : ''}
                </span>
              </span>
              <Button type="submit" variant="primary" size="sm">
                Accept invitation
              </Button>
            </ActionForm>
          ))}
        </section>
      ) : null}

      <section className="flex flex-col gap-3">
        <h2 className="m-0 text-[15px] leading-[22px] font-semibold">
          {invitations.length > 0 ? 'Or create a new organisation' : 'Create your organisation'}
        </h2>
        <p className="m-0 text-ink-muted">
          You become its Owner. You can invite your team and add workspaces from Settings.
        </p>
        <ActionForm action={createOrganisation} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="si-label">Organisation name</span>
            <input name="name" required maxLength={200} className={inputClass} placeholder="Acme Industrial" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="si-label">First workspace</span>
            <input name="workspace" maxLength={200} className={inputClass} defaultValue="Default" />
          </label>
          <div>
            <Button type="submit" variant="primary">
              Create organisation
            </Button>
          </div>
        </ActionForm>
      </section>
    </div>
  );
}

export default function OnboardingPage() {
  return (
    <main className="grid min-h-full place-items-center px-4 py-12">
      <div className="flex w-full max-w-md flex-col gap-6 rounded-lg border border-line bg-surface p-8 shadow-[var(--shadow-sm)]">
        <div className="flex flex-col gap-1">
          <span className="si-label">Signal Intel</span>
          <h1 className="m-0 text-[20px] leading-7 font-semibold tracking-tight">Get started</h1>
        </div>
        <Suspense fallback={<p className="m-0 text-ink-muted">Loading your account…</p>}>
          <Onboarding />
        </Suspense>
      </div>
    </main>
  );
}
