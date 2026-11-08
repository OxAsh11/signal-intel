import 'server-only';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext } from '@/lib/org';
import Link from 'next/link';

type BriefRow = {
  id: string;
  name: string;
  state: string;
  intake_status: string;
  version_count: number;
  current_version: number;
  updated_at: string;
};

export async function BriefList() {
  const ctx = await getOrgContext();
  const supabase = await createClient();
  if (!ctx?.workspace || !supabase) return <p className="m-0 text-ink-muted">No workspace selected.</p>;

  const { data, error } = await supabase.rpc('list_briefs', { p_ws: ctx.workspace.id }) as { data: BriefRow[] | null; error: { message: string } | null };
  if (error) return <p className="m-0 text-danger">{error.message}</p>;
  if (!data?.length) return (
    <p className="m-0 text-ink-muted">
      No Briefs yet.{' '}
      <Link href="/briefs/new" className="text-accent underline-offset-2 hover:underline">Create the first one.</Link>
    </p>
  );

  // Group by state: Draft, Active, Paused, Archived
  const groups = ['Draft', 'Active', 'Paused', 'Archived'] as const;
  const byState = Object.fromEntries(groups.map(s => [s, data.filter(b => b.state === s)]));

  return (
    <div className="flex flex-col gap-6">
      {groups.filter(s => byState[s].length > 0).map(state => (
        <section key={state}>
          <h2 className="mb-2 text-sm font-medium text-ink-muted uppercase tracking-wide">{state}</h2>
          <ul className="flex flex-col gap-2">
            {byState[state].map(b => (
              <li key={b.id}>
                <Link href={`/briefs/${b.id}`}
                  className="flex items-center justify-between rounded-md border border-line bg-surface px-4 py-3 hover:bg-surface-raised transition-colors">
                  <span className="font-medium">{b.name}</span>
                  <span className="text-xs text-ink-muted tabular-nums">
                    {b.intake_status === 'pending' ? '⏳ drafting…' :
                     b.version_count ? `v${b.current_version}` : 'draft'}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
