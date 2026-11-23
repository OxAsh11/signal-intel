import type { Metadata } from 'next';
import { Suspense } from 'react';
import { getOrgContext } from '@/lib/org';
import { BudgetMeter } from '@/components/budget-meter';
import { MembersAndAccess } from './members';
import { ApiAccess } from './api-access';
import { Providers } from './providers';
import { UsageAndBudgets } from './usage';
import { DataSection } from './_data-section';
import { HealthSection } from './_health-section';
import { BusinessSection } from './_business-section';
import { SegmentsSection } from './_segments-section';

export const metadata: Metadata = { title: 'Settings' };

const SECTIONS = ['workspace', 'business', 'mailboxes', 'integrations', 'providers', 'budget', 'data', 'health', 'api'] as const;

export default async function SettingsPage({ searchParams }: PageProps<'/settings'>) {
  const sp = await searchParams;
  const section = (sp?.section as string) || 'workspace';
  const ctx = await getOrgContext();
  return (
    <>
      <div className="flex flex-col gap-1">
        <h1 className="si-h1">Settings</h1>
        <p className="si-lede">Members and access, API tokens, integrations and webhooks, AI usage and budgets.</p>
      </div>
      <nav aria-label="Settings sections" className="flex flex-wrap gap-2 border-b border-line pb-2 text-[14px]">
        {SECTIONS.map((s) => (
          <a key={s} href={`/settings?section=${s}`} className={section === s ? 'font-semibold text-ink' : 'text-ink-muted hover:text-ink'}>
            {s}
          </a>
        ))}
      </nav>
      {section === 'workspace' ? (
        <>
          <div className="self-start md:hidden">
            <Suspense fallback={null}>
              <BudgetMeter className="border border-line" />
            </Suspense>
          </div>
          <Suspense fallback={<p className="m-0 text-ink-muted">Loading members…</p>}>
            <MembersAndAccess />
          </Suspense>
        </>
      ) : null}
      {section === 'workspace' || section === 'providers' ? (
        <Suspense fallback={<p className="m-0 text-ink-muted">Loading providers…</p>}>
          <Providers />
        </Suspense>
      ) : null}
      {section === 'budget' ? (
        <Suspense fallback={<p className="m-0 text-ink-muted">Loading usage…</p>}>
          <UsageAndBudgets searchParams={searchParams} />
        </Suspense>
      ) : null}
      {section === 'api' ? (
        <>
          <Suspense fallback={<p className="m-0 text-ink-muted">Loading API access…</p>}>
            <ApiAccess />
          </Suspense>
        </>
      ) : null}
      {section === 'business' ? (
        <Suspense fallback={null}>
          <BusinessSection ws={ctx?.workspace?.id ?? ''} />
        </Suspense>
      ) : null}
      {section === 'mailboxes' ? <p className="m-0 text-ink-muted">Arrives with the Email engine.</p> : null}
      {section === 'integrations' ? <p className="m-0 text-ink-muted">Not set up yet.</p> : null}
      {section === 'data' ? (
        <Suspense fallback={null}>
          <div className="flex flex-col gap-4">
            <DataSection ws={ctx?.workspace?.id ?? ''} />
            <SegmentsSection ws={ctx?.workspace?.id ?? ''} />
          </div>
        </Suspense>
      ) : null}
      {section === 'health' ? (
        <Suspense fallback={null}>
          <HealthSection ws={ctx?.workspace?.id ?? ''} />
        </Suspense>
      ) : null}
    </>
  );
}