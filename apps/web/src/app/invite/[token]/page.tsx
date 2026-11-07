import type { Metadata } from 'next';
import { Suspense } from 'react';
import { Button } from '@signal/ui';
import { acceptInvitationToken } from '@/app/actions/access';
import { ActionForm } from '@/components/forms';
import { getCurrentUser } from '@/lib/supabase/server';

export const metadata: Metadata = { title: 'Accept invitation' };

async function Accept({ params }: { params: Promise<{ token: string }> }) {
  const [{ token }, user] = await Promise.all([params, getCurrentUser()]);
  return (
    <ActionForm action={acceptInvitationToken} className="flex flex-col gap-3">
      <input type="hidden" name="token" value={token} />
      <p className="m-0">
        You&apos;re signed in as <strong>{user?.email ?? 'unknown'}</strong>. The invitation only works for the email
        address it was sent to.
      </p>
      <div>
        <Button type="submit" variant="primary">
          Accept invitation
        </Button>
      </div>
    </ActionForm>
  );
}

export default function InvitePage({ params }: PageProps<'/invite/[token]'>) {
  return (
    <main className="grid min-h-full place-items-center px-4 py-12">
      <div className="flex w-full max-w-md flex-col gap-4 rounded-lg border border-line bg-surface p-8 shadow-[var(--shadow-sm)]">
        <div className="flex flex-col gap-1">
          <span className="si-label">Signal Intel</span>
          <h1 className="m-0 text-[20px] leading-7 font-semibold tracking-tight">You&apos;ve been invited</h1>
        </div>
        <Suspense fallback={<p className="m-0 text-ink-muted">Checking your invitation…</p>}>
          <Accept params={params} />
        </Suspense>
      </div>
    </main>
  );
}
