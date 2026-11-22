import type { Metadata } from 'next';
import Link from 'next/link';
import { Suspense } from 'react';
import { Card, FamilyTag } from '@signal/ui';
import { getBriefLens, lensBriefs } from '@/lib/brief-lens';
import { getOrgContext } from '@/lib/org';
import { AccountsToActCard } from './_home/accounts-to-act';
import { ChangesCard } from './_home/changes';
import { CrmCard } from './_home/crm-card';
import { EmailCard } from './_home/email-card';
import { HeaderCard } from './_home/header-card';
import { Journey } from './_home/journey';
import { Metrics } from './_home/metrics';
import { NeedsYouCard } from './_home/needs-you';
import { CardSkeleton } from './_home/card-error';
import { type HomeCardProps } from './_home/shared';
import { SourcesCard } from './_home/sources-card';

export const metadata: Metadata = { title: 'Home' };

const Arrow = () => (
  <svg className="si-btn-arrow" viewBox="0 0 6.504 9.912" aria-hidden="true">
    <path d="M0.8 0 L6.5 4.956 L0.8 9.912 L0 9.1 L4.8 4.956 L0 0.8 Z" fill="currentColor" />
  </svg>
);

/** This page's URL, so a failed card's Retry reloads exactly what was asked for (lens included). */
function selfHref(params: Record<string, string | string[] | undefined>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    for (const x of Array.isArray(v) ? v : v == null ? [] : [v]) qs.append(k, x);
  }
  return qs.size ? `/?${qs.toString()}` : '/';
}

/**
 * Home: what needs me, what changed and what's next, under the Brief lens. Every area is its own
 * card reading its own RPC inside its own <Suspense>, so a slow or failing card never holds up or
 * breaks the others. A workspace with no Brief shows only the journey and where to start.
 * Colour families: pool = discovery, mint = judgement, grass = engagement, mustard = needs you,
 * lava = cost.
 */
export default async function HomePage({ searchParams }: PageProps<'/'>) {
  const params = await searchParams;
  const ctx = await getOrgContext();
  if (!ctx?.workspace) {
    return (
      <div className="flex flex-col gap-2">
        <h1 className="si-h1">Home</h1>
        <p className="si-lede">Join or create a workspace to see what the engine found.</p>
      </div>
    );
  }
  const ws = ctx.workspace.id;
  const [lens, briefs] = await Promise.all([getBriefLens(params), lensBriefs(ws)]);
  const card: HomeCardProps = { ws, brief: lens.briefId, retry: selfHref(params) };

  // A failed Brief read is not an empty workspace: say so, never fall back to onboarding.
  if (!briefs || lens.source === 'error') {
    return (
      <>
        <h1 className="si-h1">Home</h1>
        <p className="m-0 text-ink-body" role="alert" data-home-error="">
          Home could not load.{' '}
          <a href={card.retry} className="text-ink underline">Retry</a>
        </p>
      </>
    );
  }

  if (briefs.length === 0) {
    return (
      <>
        <h1 className="si-h1">Home</h1>
        <Suspense fallback={<CardSkeleton title="Getting started" lines={1} />}>
          <Journey {...card} />
        </Suspense>
        <Card family="mint" kind="feature" aria-labelledby="home-start" className="max-w-[720px]">
          <FamilyTag family="mint">Start here</FamilyTag>
          <h2 id="home-start" className="si-h2">Describe who you sell to</h2>
          <p className="m-0">
            Say what you sell and who buys it. Signal Intel drafts the Brief from that: the companies to look for, the
            sources to watch and the evidence that counts.
          </p>
          <Link href="/briefs/new" className="si-btn si-btn-primary self-start no-underline hover:no-underline">
            Describe who you sell to
            <Arrow />
          </Link>
        </Card>
      </>
    );
  }

  return (
    <>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="si-h1">Home</h1>
          <Suspense fallback={<span className="block h-7 w-64 max-w-full animate-pulse rounded-[2px] bg-surface-sunken" />}>
            <HeaderCard {...card} />
          </Suspense>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href="/accounts" className="si-btn no-underline hover:no-underline">Open accounts</Link>
          <Link href="/email/sequences" className="si-btn si-btn-primary no-underline hover:no-underline">
            New sequence
            <Arrow />
          </Link>
        </div>
      </header>

      <Suspense fallback={<CardSkeleton title="Getting started" lines={1} />}>
        <Journey {...card} />
      </Suspense>

      <Suspense fallback={<CardSkeleton title="Key numbers" lines={2} />}>
        <Metrics {...card} />
      </Suspense>

      {/* Two columns on desktop, one below 1180px (the phone included). Layout on wrappers only. */}
      <div className="grid grid-cols-1 items-start gap-6 min-[1180px]:grid-cols-[minmax(0,1fr)_minmax(300px,380px)]">
        <div className="flex min-w-0 flex-col gap-6">
          <Suspense fallback={<CardSkeleton title="Needs you" lines={4} />}>
            <NeedsYouCard {...card} />
          </Suspense>
          <Suspense fallback={<CardSkeleton title="Accounts to act on" lines={5} />}>
            <AccountsToActCard {...card} />
          </Suspense>
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <Suspense fallback={<CardSkeleton title="What changed" lines={4} />}>
            <ChangesCard {...card} />
          </Suspense>
          <EmailCard />
          <Suspense fallback={<CardSkeleton title="CRM pipeline" lines={4} />}>
            <CrmCard {...card} />
          </Suspense>
          <Suspense fallback={<CardSkeleton title="Sources" lines={3} />}>
            <SourcesCard {...card} />
          </Suspense>
        </div>
      </div>
    </>
  );
}
