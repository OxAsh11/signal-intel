import type { Metadata } from 'next';
import { Suspense } from 'react';
import { BudgetMeter } from '@/components/budget-meter';
import { ApiAccess } from './api-access';
import { MembersAndAccess } from './members';
import { Providers } from './providers';
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
      {/* Phone: the sidebar row has no room for the budget meter, so Settings shows it. */}
      <div className="self-start md:hidden">
        <Suspense fallback={null}>
          <BudgetMeter className="border border-line" />
        </Suspense>
      </div>
      <Suspense fallback={<p className="m-0 text-ink-muted">Loading members…</p>}>
        <MembersAndAccess />
      </Suspense>
      <Suspense fallback={<p className="m-0 text-ink-muted">Loading API access…</p>}>
        <ApiAccess />
      </Suspense>
      <Suspense fallback={<p className="m-0 text-ink-muted">Loading providers…</p>}>
        <Providers />
      </Suspense>
      <Suspense fallback={<p className="m-0 text-ink-muted">Loading usage…</p>}>
        <UsageAndBudgets searchParams={searchParams} />
      </Suspense>
    </>
  );
}
