import Link from 'next/link';
import { Card } from '@signal/ui';

/** The Email engine has not shipped: an honest placeholder, never sample numbers. */
export function EmailCard() {
  return (
    <Card aria-labelledby="home-email">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="home-email" className="si-h2">Email engine</h2>
        <span className="rounded-[4px] bg-surface-sunken border border-line px-2 py-0.5 text-[11px] font-medium text-ink-muted">Coming soon</span>
      </div>
      <p className="m-0 text-[13px] text-ink-body">Sending, approvals and replies arrive with the Email engine.</p>
      <Link href="/email" className="self-start text-[13px] text-ink underline">
        Open Email engine
      </Link>
    </Card>
  );
}
