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
      <Link href="/briefs/new" className="font-medium text-ink underline">Create the first one.</Link>
    </p>
  );

  // Group by state; include Pilot for forward-compat and an Other bucket for unexpected values
  const groups = ['Draft', 'Active', 'Pilot', 'Paused', 'Archived'] as const;
  const knownStates = new Set<string>(groups);
  const byState: Record<string, BriefRow[]> = Object.fromEntries(groups.map(s => [s, data.filter(b => b.state === s)]));
  byState['Other'] = data.filter(b => !knownStates.has(b.state));

  const STATE_DOT: Record<string, string> = {
    Active: 'bg-positive',
    Pilot: 'bg-signal',
    Draft: 'bg-ink-faint',
    Paused: 'bg-ink-faint',
    Archived: 'bg-ink-faint',
  };

  return (
    <div className="flex flex-col gap-5">
      {([...groups, 'Other'] as const).filter(s => (byState[s]?.length ?? 0) > 0).map(state => (
        <section key={state}>
          <h2 className="si-label mb-1.5">{state}</h2>
          <ul className="flex flex-col rounded-md border border-line overflow-hidden">
            {byState[state].map((b, i) => {
              const statusText =
                b.intake_status === 'pending' ? 'Drafting…' :
                b.intake_status === 'failed' ? 'Intake failed' :
                b.version_count ? `v${b.current_version}` : 'Draft';
              return (
                <li key={b.id} className={i ? 'border-t border-line' : undefined}>
                  <Link href={`/briefs/${b.id}`}
                    className="flex items-center gap-3 px-3 py-2.5 text-[13px] text-ink no-underline hover:bg-surface-sunken hover:no-underline transition-colors">
                    <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATE_DOT[state] ?? 'bg-ink-faint'}`} />
                    <span className="flex-1 font-medium truncate">{b.name}</span>
                    <span className="text-[12px] text-ink-muted tabular-nums">{statusText}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
