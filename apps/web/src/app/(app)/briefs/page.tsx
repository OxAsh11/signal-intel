import type { Metadata } from 'next';
import { Suspense } from 'react';
import Link from 'next/link';
import { atLeast, getOrgContext } from '@/lib/org';
import { BriefList } from './brief-list';

export const metadata: Metadata = { title: 'Brief Studio' };

export default async function BriefStudioPage() {
  const ctx = await getOrgContext();
  const canEdit = atLeast(ctx?.workspaceRole, 'Editor');
  return (
    <>
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="m-0 text-[28px] leading-[34px] font-semibold tracking-[-0.015em]">Brief Studio</h1>
          <p className="m-0 max-w-[65ch] text-ink-muted">
            Define what you are looking for. Each Brief is a versioned hypothesis the platform collects and scores accounts against.
          </p>
        </div>
        {canEdit && (
          <Link href="/briefs/new"
            className="si-btn si-btn-primary">
            New Brief
          </Link>
        )}
      </div>
      <Suspense fallback={<p className="m-0 text-ink-muted">Loading…</p>}>
        <BriefList />
      </Suspense>
    </>
  );
}
