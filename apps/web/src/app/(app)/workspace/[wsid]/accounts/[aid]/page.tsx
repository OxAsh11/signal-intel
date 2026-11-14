import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { BandChip, Card, DerivedValue, EvidenceQuote, FamilyTag, ScoreBreakdown, StageBadge, VerdictBadge } from '@signal/ui';
import type { Band, Stage, Verdict } from '@signal/contracts';
import { createClient } from '@/lib/supabase/server';

export const metadata: Metadata = { title: 'Account' };

type ClaimRow = {
  id: string;
  quote: string | null;
  observed_at: string;
  predicate: { key: string; label: string } | { key: string; label: string }[] | null;
  source: { id: string; url: string | null; source_type: string } | { id: string; url: string | null; source_type: string }[] | null;
};
const one = <T,>(v: T | T[] | null | undefined): T | null => (Array.isArray(v) ? (v[0] ?? null) : (v ?? null));

/** Relative age in plain words ("2 days ago"); the exact time is in the title attribute. */
function ago(iso: string | null | undefined, now: number): string {
  if (!iso) return 'unknown date';
  const days = Math.floor((now - new Date(iso).getTime()) / 864e5);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  return months < 12 ? `${months} month${months > 1 ? 's' : ''} ago` : `${Math.floor(months / 12)} year${months >= 24 ? 's' : ''} ago`;
}
function nowMs(): number {
  return Date.now();
}
const host = (url: string | null) => {
  try {
    return url ? new URL(url).hostname.replace(/^www\./, '') : 'stored source';
  } catch {
    return 'stored source';
  }
};

/**
 * The account page: the evidence instrument. Verdict and score with their receipts, who to
 * contact, and the next step. Families: mint = judgement, grass = engagement, pool = research.
 */
export default async function AccountDetailPage({ params }: { params: Promise<{ wsid: string; aid: string }> }) {
  const { wsid, aid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;
  const now = nowMs();

  const { data: account } = await supabase
    .from('account')
    .select('id, stage, entity:entity_id(id, display_name)')
    .eq('id', aid)
    .eq('workspace_id', wsid)
    .maybeSingle();
  if (!account) notFound();
  const entity = one(account.entity as { id: string; display_name: string } | { id: string; display_name: string }[] | null);
  const entityId = entity?.id ?? null;

  const [{ data: scores }, { data: verdicts }, { data: labels }, { data: cards }, { data: research }, { data: contacts }, { data: stageEvents }, { data: activities }, { data: domains }] =
    await Promise.all([
      supabase.from('score').select('band, fit, evidence, scale, timing, priority, readiness, gates, computed_at, brief_version_id').eq('account_id', aid).order('computed_at', { ascending: false }).limit(1),
      entityId
        ? supabase.from('verdict').select('value, claim_ids, computed_at, inputs, brief_version:brief_version_id(version)').eq('entity_id', entityId).order('computed_at', { ascending: false }).limit(1)
        : Promise.resolve({ data: null }),
      supabase.from('label').select('id, kind, value').eq('account_id', aid).order('kind'),
      supabase.from('account_card').select('content, computed_at').eq('account_id', aid).order('computed_at', { ascending: false }).limit(1),
      supabase.from('research_doc').select('id, version, published_at').eq('account_id', aid).order('published_at', { ascending: false }),
      supabase.from('contact_point').select('id, type, status, provider, person:entity_id(display_name)').eq('account_id', aid),
      supabase.from('account_stage_event').select('id, to_stage, occurred_at').eq('account_id', aid).order('occurred_at', { ascending: false }).limit(20),
      supabase.from('activity').select('id, kind, occurred_at, person:person_entity_id(display_name)').eq('account_id', aid).order('occurred_at', { ascending: false }).limit(20),
      entityId ? supabase.from('entity_alias').select('value_normalised').eq('entity_id', entityId).eq('alias_type', 'domain').limit(1) : Promise.resolve({ data: null }),
    ]);

  const score = scores?.[0] ?? null;
  const verdict = verdicts?.[0] ?? null;
  const card = (cards?.[0]?.content ?? null) as { bullets?: string[]; next_step?: string } | null;
  const claimIds = (verdict?.claim_ids as string[] | null) ?? [];
  const { data: claimRows } = claimIds.length
    ? await supabase
        .from('claim')
        .select('id, quote, observed_at, predicate:predicate_id(key, label), source:source_record_id(id, url, source_type)')
        .in('id', claimIds)
        .order('observed_at', { ascending: false })
    : { data: [] };
  const claims = (claimRows ?? []) as ClaimRow[];
  const version = one(verdict?.brief_version as { version: number } | { version: number }[] | null)?.version;
  const domain = domains?.[0]?.value_normalised as string | undefined;

  const timeline = [
    ...(stageEvents ?? []).map((e) => ({ id: `s${e.id}`, at: e.occurred_at as string, text: `Stage moved to ${e.to_stage}` })),
    ...(activities ?? []).map((a) => {
      const person = one(a.person as { display_name: string } | { display_name: string }[] | null)?.display_name;
      return { id: `a${a.id}`, at: a.occurred_at as string, text: `${String(a.kind).replace(/_/g, ' ')}${person ? ` · ${person}` : ''}` };
    }),
  ].sort((x, y) => (x.at < y.at ? 1 : -1));

  return (
    <>
      <div className="flex flex-col gap-3">
        <Link href={`/workspace/${wsid}/accounts`} className="text-[13px] text-ink-muted">‹ Accounts</Link>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex min-w-0 flex-col gap-2">
            <h1 className="si-h1">{entity?.display_name ?? 'Unknown company'}</h1>
            {domain ? <span className="si-meta">{domain}</span> : null}
            <div className="mt-1 flex flex-wrap items-center gap-3">
              {verdict ? <VerdictBadge verdict={verdict.value as Verdict} /> : <span className="si-meta">no verdict yet</span>}
              {score ? <BandChip band={score.band as Band} /> : null}
              <StageBadge stage={account.stage as Stage} />
            </div>
          </div>
        </div>
      </div>

      <div className="si-cols">
        <div className="flex min-w-0 flex-col gap-6">
          <Card family="mint" kind="feature" aria-labelledby="why-h" className="min-h-[260px]">
            <FamilyTag family="mint">Judgement · why now</FamilyTag>
            <h2 id="why-h" className="si-h2 text-[22px]">Why this account, now</h2>
            {card?.bullets?.length ? (
              <ul className="m-0 flex max-w-[80ch] flex-col gap-1.5 pl-5 text-[16px] leading-[1.6]">
                {card.bullets.map((b, i) => <li key={i}>{b}</li>)}
              </ul>
            ) : (
              <p className="m-0">The account summary is written once the account has a verdict and a score.</p>
            )}
            <div className="flex-1" />
            {verdict ? (
              <div>
                <DerivedValue
                  label="Verdict"
                  versions={version ? `brief v${version}` : undefined}
                  explanation={`Rests on ${claimIds.length} claim${claimIds.length === 1 ? '' : 's'}. Each quote below is the exact text the verdict uses.`}
                >
                  {verdict.value} · {claimIds.length} claim{claimIds.length === 1 ? '' : 's'}
                </DerivedValue>
              </div>
            ) : null}
          </Card>

          <section aria-labelledby="ev-h" className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-3">
              <FamilyTag family="mint">Evidence</FamilyTag>
              <h2 id="ev-h" className="si-h2">{claims.length ? `${claims.length} claim${claims.length === 1 ? '' : 's'}, newest ${ago(claims[0]?.observed_at, now)}` : 'No claims yet'}</h2>
            </div>
            {claims.length ? (
              <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,420px),1fr))] gap-4">
                {claims.map((c) => {
                  const pred = one(c.predicate);
                  const src = one(c.source);
                  return (
                    <EvidenceQuote
                      key={c.id}
                      claim={pred?.label ?? pred?.key}
                      sourceType={(src?.source_type ?? 'source').replace(/_/g, ' ')}
                      sourceName={host(src?.url ?? null)}
                      sourceHref={src?.url ?? undefined}
                      observed={ago(c.observed_at, now)}
                    >
                      {c.quote ? <mark>{c.quote}</mark> : 'No quote: structured source'}
                    </EvidenceQuote>
                  );
                })}
              </div>
            ) : (
              <p className="m-0 text-ink-muted">Claims appear as radars find pages about this company.</p>
            )}
          </section>

          <Card className="si-flush" aria-labelledby="act-h">
            <h2 id="act-h" className="si-h2 px-6 pt-6 pb-2">Activity</h2>
            {timeline.length ? (
              <ul className="m-0 list-none p-0">
                {timeline.map((t) => (
                  <li key={t.id} className="grid grid-cols-[120px_minmax(0,1fr)] gap-3 border-t border-line px-6 py-3">
                    <span className="si-meta" title={new Date(t.at).toLocaleString()}>{ago(t.at, now)}</span>
                    <span>{t.text}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="m-0 px-6 pb-6 text-ink-muted">No activity yet.</p>
            )}
          </Card>
        </div>

        <aside className="flex flex-col gap-4" aria-label="Next step, score and people">
          <Card family="grass" kind="feature" aria-labelledby="next-h" className="min-h-[220px] p-7">
            <FamilyTag family="grass">Engagement · next step</FamilyTag>
            <h2 id="next-h" className="si-h2 text-[20px]">{card?.next_step ?? 'No next step yet'}</h2>
            <p className="m-0 text-[14px]">
              {card?.next_step ? 'Suggested from the evidence and the account’s stage.' : 'A next step is suggested once the account is scored.'}
            </p>
          </Card>

          <div className="flex flex-col gap-2">
            <FamilyTag family="mint">Judgement · score</FamilyTag>
            {score ? (
              <ScoreBreakdown
                priority={Number(score.priority)}
                band={score.band as Band}
                dimensions={[
                  { name: 'Fit', value: Number(score.fit) },
                  { name: 'Evidence', value: Number(score.evidence) },
                  { name: 'Scale', value: Number(score.scale) },
                  { name: 'Timing', value: Number(score.timing) },
                ]}
                readiness={Number(score.readiness)}
                gates={score.gates ? (score.gates as { reason: string }[]).map((g) => ({ name: g.reason, hit: true })) : undefined}
              />
            ) : (
              <Card><span className="text-ink-muted">Not scored yet.</span></Card>
            )}
          </div>

          <Card family="grass" aria-labelledby="people-h">
            <FamilyTag family="grass">Engagement · people</FamilyTag>
            <h2 id="people-h" className="si-h2">{contacts?.length ? `${contacts.length} contact point${contacts.length === 1 ? '' : 's'}` : 'No contacts yet'}</h2>
            {(contacts ?? []).map((c) => {
              const person = one(c.person as { display_name: string } | { display_name: string }[] | null)?.display_name;
              const ok = c.status === 'valid';
              return (
                <div key={c.id} className="flex flex-col">
                  <span className="font-medium text-ink">{person ?? 'Unknown person'}</span>
                  <span className={ok ? 'text-[13px] font-medium text-positive' : 'text-[13px] text-ink-muted'}>
                    {ok ? '✓' : '○'} {c.type} {String(c.status).replace(/_/g, ' ')}{c.provider ? ` · ${c.provider}` : ''}
                  </span>
                </div>
              );
            })}
            {!contacts?.length ? <p className="m-0 text-[13px]">Contacts are found automatically for High-band accounts.</p> : null}
          </Card>

          {labels?.length ? (
            <Card aria-labelledby="labels-h">
              <h2 id="labels-h" className="si-h2">Labels</h2>
              <div className="flex flex-wrap gap-2">
                {labels.map((l) => (
                  <span key={l.id} className="si-chip si-band">{l.kind}: {l.value}</span>
                ))}
              </div>
            </Card>
          ) : null}

          {research?.length ? (
            <Card family="pool" aria-labelledby="research-h">
              <FamilyTag family="pool">Research</FamilyTag>
              <h2 id="research-h" className="si-h2">Research documents</h2>
              {research.map((r) => (
                <span key={r.id} className="si-meta">v{r.version} · {ago(r.published_at as string, now)}</span>
              ))}
            </Card>
          ) : null}
        </aside>
      </div>
    </>
  );
}
