import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { getOrgContext, atLeast } from '@/lib/org';
import { IntakeForm } from '../intake-form';

export const metadata: Metadata = { title: 'New Brief' };

export default async function NewBriefPage() {
  const ctx = await getOrgContext();
  if (!atLeast(ctx?.workspaceRole, 'Editor')) redirect('/briefs');
  const workspaceId = ctx?.workspace?.id;
  if (!workspaceId) redirect('/briefs');
  return (
    <div className="mx-auto flex w-full max-w-[760px] flex-col gap-7">
      <div className="flex flex-col gap-2">
        <Link href="/briefs" className="text-[13px] text-ink-muted">‹ Briefs</Link>
        <span className="si-eyebrow">New Brief · step 1 of 2</span>
        <h1 className="si-h1">Which companies do you want to find?</h1>
        <p className="si-lede">
          Describe them the way you would to a colleague: what you sell, and what a company does in public when it is about to
          need you. The AI drafts the whole Brief; you check it on the next screen.
        </p>
      </div>
      <IntakeForm workspaceId={workspaceId} />
    </div>
  );
}
