import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ApiAccess } from './api-access';
import { MembersAndAccess } from './members';
import { UsageAndBudgets } from './usage';

export const metadata: Metadata = { title: 'Settings' };

export default function SettingsPage({ searchParams }: PageProps<'/settings'>) {
  return (
    <>
      <div className="flex flex-col gap-1">
        <h1 className="si-h1">Settings</h1>
        <p className="si-lede">
          Members and access, API tokens, integrations and webhooks, AI usage and budgets.
        </p>
      </div>
      <Suspense fallback={<p className="m-0 text-ink-muted">Loading members…</p>}>
        <MembersAndAccess />
      </Suspense>
      <Suspense fallback={<p className="m-0 text-ink-muted">Loading API access…</p>}>
        <ApiAccess />
      </Suspense>
      <Suspense fallback={<p className="m-0 text-ink-muted">Loading usage…</p>}>
        <UsageAndBudgets searchParams={searchParams} />
      </Suspense>
    </>
  );
}
