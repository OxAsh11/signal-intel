import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import { getOrgContext, atLeast } from '@/lib/org';
import { IntakeForm } from '../intake-form';
import { generateBrief } from '@/app/actions/briefs';

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
      <form action={generateBrief} className="si-card flex flex-col gap-3">
        <input type="hidden" name="workspace" value={workspaceId} />
        <span className="si-eyebrow">Generate from a website</span>
        <h2 className="si-h2">Paste a domain and let the AI read the site</h2>
        <input name="domain" type="text" required placeholder="flytbase.com" className="w-full rounded-sm border border-line-strong bg-surface px-3 py-2 text-[15px] text-ink" />
        <textarea name="description" rows={3} placeholder="Optional: what you sell and who you want (helps the draft)" className="w-full rounded-sm border border-line-strong bg-surface px-3 py-2 text-[15px] text-ink" />
        <button type="submit" className="si-btn si-btn-primary self-start">Generate from website</button>
      </form>
      <div className="flex items-center gap-3 text-[13px] text-ink-muted">
        <span className="h-px flex-1 bg-line" />
        or describe it
        <span className="h-px flex-1 bg-line" />
      </div>
      <IntakeForm workspaceId={workspaceId} />
    </div>
  );
}
