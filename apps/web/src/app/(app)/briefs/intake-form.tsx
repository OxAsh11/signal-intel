'use client';
import { useActionState, useState } from 'react';
import { Button } from '@signal/ui';
import { createBrief } from '@/app/actions/briefs';

const field = 'w-full rounded-sm border border-line-strong bg-surface px-3 py-2 text-[15px] text-ink';
const EXAMPLES: [string, string][] = [
  ['Manufacturers expanding a plant', 'We sell industrial automation. Find manufacturers that announced a new plant or a production-line expansion in the next 12 months.'],
  ['Hospitals buying new equipment', 'We supply diagnostic imaging equipment. Find hospitals and diagnostic chains that are opening a new centre or tendering for imaging equipment.'],
  ['SaaS firms hiring their first RevOps lead', 'We run revenue-operations consulting. Find B2B software companies hiring their first RevOps or sales-operations lead.'],
  ['Firms that just won a government tender', 'We provide project staffing. Find companies that recently won a large government contract and will need to scale delivery teams.'],
];

/** Brief Studio, step 1: describe in plain words; the AI drafts everything else. */
export function IntakeForm({ workspaceId }: { workspaceId: string }) {
  const [state, action, pending] = useActionState(createBrief, {});
  const [description, setDescription] = useState('');
  return (
    <form action={action} className="flex flex-col gap-7">
      <input type="hidden" name="workspace" value={workspaceId} />
      <div className="flex flex-col gap-2">
        <label htmlFor="desc" className="si-label">Description</label>
        <textarea
          id="desc"
          name="description"
          required
          rows={7}
          maxLength={3000}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="We do turnkey interior fit-out for hotels. Find hotel groups in India and the Gulf that are about to open or refurbish a property, so we can reach their projects team before the contractor is chosen."
          className={`${field} resize-y leading-[1.55]`}
        />
        <span className="text-[13px] text-ink-muted">The more concrete the public sign (a tender, an opening date, a hiring post), the more accurate the matches.</span>
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-4">
        <div className="flex flex-col gap-2">
          <label htmlFor="site" className="si-label">Your website (optional)</label>
          <input id="site" name="website" type="url" placeholder="https://yourcompany.com" className={field} />
          <span className="text-[13px] text-ink-muted">Lets the AI read your services and past clients.</span>
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor="won" className="si-label">A customer you won (optional)</label>
          <input id="won" name="customer" placeholder="Company name" className={field} />
          <span className="text-[13px] text-ink-muted">One real example sharpens the draft a lot.</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <Button type="submit" variant="primary" disabled={pending}>{pending ? 'Drafting…' : 'Draft my Brief'}</Button>
        <span className="text-[13px] text-ink-muted">About 20 seconds. Nothing is collected until you start a pilot.</span>
      </div>
      {state?.error ? <p className="m-0 text-[13px] text-danger">{state.error}</p> : null}

      <div className="si-card si-gcard si-fam-mint">
        <span className="si-ftag si-fam-mint">The AI will draft</span>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3 text-[13px] leading-[1.5]">
          <span><strong className="text-ink">The signal</strong>: the public event that marks a company as ready.</span>
          <span><strong className="text-ink">Who fits</strong>: countries, industries and size.</span>
          <span><strong className="text-ink">Examples</strong>: companies that match and ones that don&apos;t.</span>
          <span><strong className="text-ink">Where to look</strong>: news, tenders, events, registries, posts.</span>
          <span><strong className="text-ink">Who to contact</strong>: the roles that decide, use and influence.</span>
          <span><strong className="text-ink">Scoring and rules</strong>: set for you, open if you want them.</span>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <span className="si-label">Or start from an example</span>
        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map(([label, text]) => (
            <button key={label} type="button" onClick={() => setDescription(text)} className="si-ftag si-fam-pool cursor-pointer border-0 text-[13px]">
              {label}
            </button>
          ))}
        </div>
      </div>
    </form>
  );
}
