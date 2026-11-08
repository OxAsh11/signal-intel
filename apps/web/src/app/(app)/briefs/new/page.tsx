import type { Metadata } from 'next';
import { getOrgContext, atLeast } from '@/lib/org';
import { redirect } from 'next/navigation';
import { IntakeForm } from '../intake-form';

export const metadata: Metadata = { title: 'New Brief' };

export default async function NewBriefPage() {
  const ctx = await getOrgContext();
  if (!atLeast(ctx?.workspaceRole, 'Editor')) redirect('/briefs');
  return (
    <>
      <h1 className="m-0 text-[28px] leading-[34px] font-semibold tracking-[-0.015em]">New Brief</h1>
      <p className="m-0 max-w-[65ch] text-ink-muted">
        Describe your hypothesis in plain language. The AI drafts the structured fields; you review and edit each one before publishing.
      </p>
      <IntakeForm workspaceId={ctx!.workspace!.id} />
    </>
  );
}
