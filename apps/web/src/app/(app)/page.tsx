import type { Metadata } from 'next';
import Link from 'next/link';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext } from '@/lib/org';

export const metadata: Metadata = { title: 'Home' };

const STAGES = ['Untouched', 'Queued', 'Contacted', 'Engaged', 'Qualified', 'Meeting', 'Handed off', 'Dormant', 'Disqualified'];

function Tile({ label, value, href }: { label: string; value: number | null; href: string }) {
  return (
    <Link href={href} className="flex min-w-[150px] flex-col gap-1 rounded-md border border-line bg-surface p-4 text-ink no-underline">
      <span className="si-label">{label}</span>
      <span className="text-[28px] leading-[34px] font-semibold">{value ?? '—'}</span>
    </Link>
  );
}

/** Daily briefing: what is waiting for a person, and where the accounts stand. Every number links to its rows. */
export default async function HomePage() {
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) {
    return (
      <div className="flex flex-col gap-1">
        <h1 className="m-0 text-[28px] leading-[34px] font-semibold tracking-[-0.015em]">Home</h1>
        <p className="m-0 text-ink-muted">Join or create a workspace to see its briefing.</p>
      </div>
    );
  }
  const ws = ctx.workspace.id;
  const base = `/workspace/${ws}`;
  const rows = (table: string) => supabase.from(table).select('id', { count: 'exact', head: true }).eq('workspace_id', ws);
  const count = async (query: PromiseLike<{ count: number | null; error: unknown }>): Promise<number | null> => {
    const { count: n, error } = await query;
    return error ? null : (n ?? 0);
  };

  const [accounts, reviewItems, unclassified, openTasks, unconfirmed, activeCampaigns, pausedCampaigns, troubledRadars, stageRows] =
    await Promise.all([
      count(rows('account')),
      count(rows('review_item').eq('status', 'pending')),
      count(rows('activity').eq('kind', 'reply_received').is('payload->>reply_class', null)),
      count(rows('task').eq('status', 'open')),
      count(rows('meeting').eq('proof_status', 'unconfirmed')),
      count(rows('campaign').eq('status', 'active')),
      count(rows('campaign').eq('status', 'paused')),
      count(rows('radar').in('status', ['silent', 'error'])),
      supabase.from('account').select('stage').eq('workspace_id', ws),
    ]);

  const byStage: Record<string, number> = {};
  for (const row of (stageRows.data ?? []) as { stage: string }[]) byStage[row.stage] = (byStage[row.stage] ?? 0) + 1;

  return (
    <>
      <div className="flex flex-col gap-1">
        <h1 className="m-0 text-[28px] leading-[34px] font-semibold tracking-[-0.015em]">Home</h1>
        <p className="m-0 max-w-[65ch] text-ink-muted">{ctx.workspace.name}: what is waiting for you, and where the accounts stand.</p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="m-0 text-lg font-semibold">Waiting for a person</h2>
        <div className="flex flex-wrap gap-3">
          <Tile label="Identity matches to review" value={reviewItems} href="/review" />
          <Tile label="Replies to classify" value={unclassified} href={`${base}/inbox`} />
          <Tile label="Meetings to confirm" value={unconfirmed} href={`${base}/meetings`} />
          <Tile label="Open tasks" value={openTasks} href={`${base}/meetings`} />
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="m-0 text-lg font-semibold">Health</h2>
        <div className="flex flex-wrap gap-3">
          <Tile label="Radars silent or failing" value={troubledRadars} href="/discovery" />
          <Tile label="Campaigns paused" value={pausedCampaigns} href={`${base}/campaigns`} />
          <Tile label="Campaigns active" value={activeCampaigns} href={`${base}/campaigns`} />
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="m-0 text-lg font-semibold">Accounts by stage</h2>
        {accounts === 0 ? (
          <p className="m-0 text-ink-muted">
            No accounts yet. Publish a Brief and add a radar in <Link href="/briefs">Brief Studio</Link>.
          </p>
        ) : (
          <div className="flex flex-wrap gap-3">
            {STAGES.filter((s) => byStage[s]).map((s) => (
              <Tile key={s} label={s} value={byStage[s]} href={`${base}/accounts`} />
            ))}
          </div>
        )}
      </section>
    </>
  );
}
