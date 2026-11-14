import type { Metadata } from 'next';
import Link from 'next/link';
import { BandChip, Card, FamilyTag, HealthIndicator, MetricTile, StageBadge, VerdictBadge } from '@signal/ui';
import type { HealthStatus } from '@signal/ui';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext } from '@/lib/org';

export const metadata: Metadata = { title: 'Today' };

const STAGES = ['Untouched', 'Queued', 'Contacted', 'Engaged', 'Qualified', 'Meeting', 'Handed off', 'Dormant', 'Disqualified'] as const;
type Stage = (typeof STAGES)[number];
type Verdict = 'Confirmed' | 'Likely' | 'Unverified' | 'Contradicted';
type Band = 'High' | 'Medium' | 'Low' | 'Excluded';
type Quote = { quote?: string | null };

const RADAR_HEALTH: Record<string, HealthStatus> = { active: 'ok', silent: 'silent', error: 'incident' };
const nf = new Intl.NumberFormat('en');
const fmt = (n: number | null) => (n == null ? '—' : nf.format(n));
/** ISO timestamp `days` days before now (a request-time value, computed once per render). */
function daysAgo(days: number): string {
  return new Date(Date.now() - days * 864e5).toISOString();
}

/**
 * Today: what the engine found this week, the evidence behind it, and what waits for a person.
 * Colour families: mint = judgement, pool = discovery, grass = engagement, mustard = needs you,
 * lava = alerts. Every number links to its rows.
 */
export default async function TodayPage() {
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) {
    return (
      <div className="flex flex-col gap-2">
        <h1 className="si-h1">Today</h1>
        <p className="si-lede">Join or create a workspace to see what the engine found.</p>
      </div>
    );
  }
  const ws = ctx.workspace.id;
  const base = `/workspace/${ws}`;
  const since = daysAgo(7);
  const rows = (table: string) => supabase.from(table).select('id', { count: 'exact', head: true }).eq('workspace_id', ws);
  const count = async (query: PromiseLike<{ count: number | null; error: unknown }>): Promise<number | null> => {
    const { count: n, error } = await query;
    return error ? null : (n ?? 0);
  };

  const [
    fetched, prefiltered, extracted, matched, confirmed,
    reviewItems, verdictReviews, unmatchedMeetings, unclassified, openTasks, unconfirmedMeetings, troubledRadars,
    stageRows, radarRows, verdictRows,
  ] = await Promise.all([
    count(rows('source_record').gte('created_at', since)),
    count(rows('source_record').gte('created_at', since).eq('status_detail', 'prefilter')),
    count(rows('source_record').gte('created_at', since).eq('extraction_status', 'extracted')),
    count(rows('verdict').gte('computed_at', since).in('value', ['Confirmed', 'Likely'])),
    count(rows('verdict').gte('computed_at', since).eq('value', 'Confirmed')),
    count(rows('review_item').eq('status', 'pending')),
    count(rows('verdict_review').eq('status', 'pending')),
    count(rows('meeting').is('account_id', null)),
    count(rows('activity').eq('kind', 'reply_received').is('payload->>reply_class', null)),
    count(rows('task').eq('status', 'open')),
    count(rows('meeting').eq('proof_status', 'unconfirmed')),
    count(rows('radar').in('status', ['silent', 'error'])),
    supabase.from('account').select('stage').eq('workspace_id', ws),
    supabase.from('radar').select('id, name, status, record_count, brief_id').eq('workspace_id', ws).neq('status', 'paused').order('created_at').limit(6),
    supabase
      .from('verdict')
      .select('id, entity_id, value, quotes, computed_at, entity:entity_id ( display_name )')
      .eq('workspace_id', ws)
      .in('value', ['Confirmed', 'Likely'])
      .order('computed_at', { ascending: false })
      .limit(6),
  ]);

  const byStage: Partial<Record<Stage, number>> = {};
  for (const r of (stageRows.data ?? []) as { stage: Stage }[]) byStage[r.stage] = (byStage[r.stage] ?? 0) + 1;

  // Accounts, stages and bands for the newest matches.
  const verdicts = (verdictRows.data ?? []) as {
    id: string; entity_id: string; value: Verdict; quotes: Quote[] | null; computed_at: string;
    entity: { display_name: string } | { display_name: string }[] | null;
  }[];
  const entityIds = [...new Set(verdicts.map((v) => v.entity_id))];
  const { data: accountRows } = entityIds.length
    ? await supabase.from('account').select('id, entity_id, stage').eq('workspace_id', ws).in('entity_id', entityIds)
    : { data: [] };
  const accountByEntity = new Map((accountRows ?? []).map((a) => [a.entity_id as string, a as { id: string; stage: Stage }]));
  const accountIds = (accountRows ?? []).map((a) => a.id as string);
  const { data: scoreRows } = accountIds.length
    ? await supabase.from('score').select('account_id, band, computed_at').in('account_id', accountIds).order('computed_at', { ascending: false })
    : { data: [] };
  const bandByAccount = new Map<string, Band>();
  for (const s of (scoreRows ?? []) as { account_id: string; band: Band }[]) if (!bandByAccount.has(s.account_id)) bandByAccount.set(s.account_id, s.band);

  const needs = [
    { n: (reviewItems ?? 0), label: 'possible duplicates to check', href: '/review' },
    { n: (verdictReviews ?? 0), label: 'verdicts to review', href: '/review' },
    { n: (unmatchedMeetings ?? 0), label: 'meetings to match to an account', href: '/review' },
    { n: (unclassified ?? 0), label: 'replies to classify', href: `${base}/inbox` },
    { n: (unconfirmedMeetings ?? 0), label: 'meetings to confirm', href: `${base}/meetings` },
  ].filter((x) => x.n > 0);
  const needsTotal = needs.reduce((a, x) => a + x.n, 0);

  const headline =
    (matched ?? 0) > 0
      ? `${fmt(matched)} ${matched === 1 ? 'company matches' : 'companies match'} your Briefs this week. ${fmt(confirmed)} ${confirmed === 1 ? 'is' : 'are'} Confirmed.`
      : 'No new matches this week yet.';
  const lede =
    (fetched ?? 0) > 0
      ? `The engine read ${fmt(fetched)} pages, kept ${fmt((fetched ?? 0) - (prefiltered ?? 0))} that mentioned what you look for, and judged them against your hypothesis. Every match shows the words it rests on.`
      : 'Radars collect pages from news, tenders, events, registries and public posts. Start a pilot in Brief Studio to see the first matches here.';

  return (
    <>
      <Card family="mint" kind="feature" aria-labelledby="today-h" className="min-h-[280px]">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <FamilyTag family="mint">Matches · last 7 days</FamilyTag>
          <span className="si-meta">{ctx.workspace.name}</span>
        </div>
        <h1 id="today-h" className="si-h1 max-w-[30ch]">{headline}</h1>
        <p className="si-lede max-w-[70ch]">{lede}</p>
        <div className="flex-1" />
        <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-4">
          {[
            [fetched, 'pages fetched'],
            [fetched == null || prefiltered == null ? null : fetched - prefiltered, 'passed the key-term filter'],
            [extracted, 'judged relevant by AI'],
            [matched, 'matches (Confirmed or Likely)'],
          ].map(([n, label]) => (
            <div key={label as string} className="flex flex-col">
              <dd className="m-0 font-mono text-[28px] leading-[34px] text-ink">{fmt(n as number | null)}</dd>
              <dt className="text-[13px] text-ink">{label as string}</dt>
            </div>
          ))}
        </dl>
      </Card>

      <div className="si-tiles">
        <MetricTile family="pool" label="Pages read" value={fmt(fetched)} href="/discovery" delta="last 7 days" version="def v1" />
        <MetricTile family="mint" label="Matches" value={fmt(matched)} href={`${base}/accounts`} delta={`${fmt(confirmed)} Confirmed`} version="def v1" />
        <MetricTile family="mint" label="Accounts" value={fmt((stageRows.data ?? []).length)} href={`${base}/accounts`} delta="in this workspace" version="def v1" />
        <MetricTile family="grass" label="Replies to classify" value={fmt(unclassified)} href={`${base}/inbox`} version="def v1" />
        <MetricTile family="grass" label="Open tasks" value={fmt(openTasks)} href={`${base}/meetings`} version="def v1" />
        <MetricTile family="lava" label="Radars failing" value={fmt(troubledRadars)} href="/discovery" delta="silent or in error" version="def v1" />
      </div>

      <div className="si-cols">
        <Card family="mint" className="si-flush" aria-labelledby="matches-h">
          <div className="flex flex-wrap items-center justify-between gap-3 p-6 pb-4">
            <div className="flex flex-wrap items-center gap-3">
              <FamilyTag family="mint">Judgement</FamilyTag>
              <h2 id="matches-h" className="si-h2">New matches</h2>
            </div>
            <Link href={`${base}/accounts`} className="text-[13px]">All accounts</Link>
          </div>
          {verdicts.length === 0 ? (
            <p className="m-0 px-6 pb-6 text-ink-muted">No Confirmed or Likely companies yet. They appear here as soon as a radar finds evidence.</p>
          ) : (
            <div className="si-table-box bg-surface">
              <table className="si-table min-w-[760px]">
                <thead>
                  <tr><th>Company</th><th>Verdict</th><th>Band</th><th>Why now</th><th>Stage</th></tr>
                </thead>
                <tbody>
                  {verdicts.map((v) => {
                    const entity = Array.isArray(v.entity) ? v.entity[0] : v.entity;
                    const account = accountByEntity.get(v.entity_id);
                    const band = account ? bandByAccount.get(account.id) : undefined;
                    const quote = (v.quotes ?? []).find((q) => q.quote)?.quote;
                    return (
                      <tr key={v.id}>
                        <td>
                          {account ? (
                            <Link href={`${base}/accounts/${account.id}`} className="font-medium text-ink">{entity?.display_name ?? 'Unknown company'}</Link>
                          ) : (
                            <span className="font-medium text-ink">{entity?.display_name ?? 'Unknown company'}</span>
                          )}
                        </td>
                        <td><VerdictBadge verdict={v.value} /></td>
                        <td>{band ? <BandChip band={band} /> : <span className="si-meta">not scored yet</span>}</td>
                        <td className="max-w-[46ch]">
                          {quote ? <q className="font-serif text-[14px] leading-[21px] text-ink">{quote}</q> : <span className="si-meta">structured source, no quote</span>}
                        </td>
                        <td>{account ? <StageBadge stage={account.stage} /> : null}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <aside className="flex flex-col gap-4" aria-label="Needs you, radars and stages">
          <Card family="mustard" aria-labelledby="needs-h">
            <div className="flex items-center gap-3">
              <FamilyTag family="mustard">Needs you</FamilyTag>
              <span className="si-meta">{needsTotal} waiting</span>
            </div>
            <h2 id="needs-h" className="si-h2">{needsTotal ? 'Decisions only you can make' : 'Nothing waits for you'}</h2>
            {needs.length ? (
              <ul className="m-0 flex list-none flex-col gap-2 p-0">
                {needs.map((x) => (
                  <li key={x.label}>
                    <Link href={x.href} className="text-ink"><span className="font-mono">{x.n}</span> {x.label}</Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="m-0 text-[13px]">Review queues are empty. New items appear when the engine is unsure.</p>
            )}
          </Card>

          <Card family="pool" aria-labelledby="radars-h">
            <FamilyTag family="pool">Discovery</FamilyTag>
            <h2 id="radars-h" className="si-h2">Radars</h2>
            {(radarRows.data ?? []).length ? (
              (radarRows.data ?? []).map((r) => (
                <HealthIndicator key={r.id as string} name={r.name as string} status={RADAR_HEALTH[r.status as string] ?? 'degraded'} detail={`${fmt(r.record_count as number)} pages`} />
              ))
            ) : (
              <p className="m-0 text-[13px]">No radars yet. Publishing a Brief creates them from its sources.</p>
            )}
            <Link href="/discovery" className="text-[13px] text-ink underline">Open Discovery</Link>
          </Card>

          <Card family="grass" aria-labelledby="stages-h">
            <FamilyTag family="grass">Engagement</FamilyTag>
            <h2 id="stages-h" className="si-h2">Accounts by stage</h2>
            {STAGES.some((s) => byStage[s]) ? (
              <ul className="m-0 flex list-none flex-col gap-2 p-0">
                {STAGES.filter((s) => byStage[s]).map((s) => (
                  <li key={s} className="flex items-center justify-between gap-3">
                    <StageBadge stage={s} />
                    <Link href={`${base}/accounts`} className="font-mono text-ink">{fmt(byStage[s] ?? 0)}</Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="m-0 text-[13px]">No accounts yet.</p>
            )}
          </Card>
        </aside>
      </div>
    </>
  );
}
