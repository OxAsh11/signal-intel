import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { Button, EvidenceQuote, FamilyTag } from '@signal/ui';
import { atLeast, getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';
import {
  classifyOverdueReply,
  createNewAction,
  matchMeeting,
  mergeAction,
  resolveVerdictReview,
  skipAction,
} from './review-actions';

export const metadata: Metadata = { title: 'Review' };

const VERDICTS = ['Confirmed', 'Likely', 'Unverified', 'Contradicted'];
const REPLY_CLASSES = ['Interested', 'Referral', 'Not now', 'Not interested', 'Out of office', 'Unsubscribe'];
const field =
  'h-8 rounded-sm border border-line-strong bg-surface px-2.5 text-[14px] text-ink placeholder:text-ink-faint';

type One<T> = T | T[] | null;
const one = <T,>(v: One<T>): T | null => (Array.isArray(v) ? (v[0] ?? null) : v);

type IdentityRow = {
  id: string;
  match_method: string | null;
  match_confidence: number | null;
  created_at: string;
  mention: One<{ raw_name: string | null; raw_domain: string | null; raw_country: string | null; entity_type: string | null }>;
  entity: One<{ display_name: string | null }>;
};
type VerdictReviewRow = {
  id: string;
  reason: 'conflict' | 'sample';
  claim_ids: string[];
  created_at: string;
  verdict: One<{
    id: string;
    value: string;
    claim_ids: string[];
    entity: One<{ display_name: string | null }>;
    brief_version: One<{ brief: One<{ name: string | null }> }>;
  }>;
};
type ClaimRow = {
  id: string;
  value: unknown;
  quote: string | null;
  confidence: number;
  observed_at: string;
  predicate: One<{ label: string | null; key: string }>;
  source: One<{ url: string | null }>;
};
type ReplyRow = {
  id: string;
  occurred_at: string;
  payload: Record<string, unknown> | null;
  person: One<{ display_name: string | null }>;
  account: One<{ entity: One<{ display_name: string | null }> }>;
};
type MeetingRow = { id: string; scheduled_at: string; external_id: string | null; person: One<{ display_name: string | null }> };
type AccountRow = { id: string; entity: One<{ display_name: string | null }> };

/** Replies received before this instant have waited more than two working days (M10). */
async function overdueCutoff(): Promise<string> {
  const d = new Date();
  for (let left = 2; left > 0; ) {
    d.setUTCDate(d.getUTCDate() - 1);
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) left -= 1;
  }
  return d.toISOString();
}

const day = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
const shown = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v));

function Section({ title, hint, count, children }: { title: string; hint: string; count: number; children: ReactNode }) {
  if (count === 0) return null;
  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <FamilyTag family="mustard">Needs you</FamilyTag>
          <h2 className="si-h2">
            {title} <span className="font-mono text-ink-muted">{count}</span>
          </h2>
        </div>
        <p className="m-0 max-w-[70ch] text-[14px] text-ink-muted">{hint}</p>
      </div>
      {children}
    </section>
  );
}

function Card({ children }: { children: ReactNode }) {
  return <div className="si-card">{children}</div>;
}

function VerdictChip({ value }: { value: string }) {
  return <span className={`si-chip si-verdict-${value.toLowerCase()}`}>{value}</span>;
}

function ClaimList({ claims }: { claims: ClaimRow[] }) {
  if (claims.length === 0) return <p className="m-0 text-[13px] text-ink-muted">No claims behind this verdict.</p>;
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,360px),1fr))] gap-3">
      {claims.map((c) => {
        const url = one(c.source)?.url ?? undefined;
        let host = 'stored source';
        try {
          if (url) host = new URL(url).hostname.replace(/^www\./, '');
        } catch {}
        return (
          <EvidenceQuote
            key={c.id}
            claim={`${one(c.predicate)?.label ?? one(c.predicate)?.key ?? 'Claim'} = ${shown(c.value)}`}
            sourceType={`confidence ${c.confidence}`}
            sourceName={host}
            sourceHref={url}
            observed={day(c.observed_at)}
          >
            {c.quote ? <mark>{c.quote}</mark> : 'No quote: structured source'}
          </EvidenceQuote>
        );
      })}
    </div>
  );
}

function VerdictForm({ reviewId, current }: { reviewId: string; current: string }) {
  return (
    <form action={resolveVerdictReview.bind(null, reviewId)} className="flex flex-wrap items-end gap-2">
      <label className="flex flex-col gap-1">
        <span className="si-label">Your decision</span>
        <select name="value" className={field} defaultValue="keep">
          <option value="keep">{current} is right</option>
          {VERDICTS.filter((v) => v !== current).map((v) => (
            <option key={v} value={v}>
              Should be {v}
            </option>
          ))}
        </select>
      </label>
      <label className="flex min-w-[200px] flex-1 flex-col gap-1">
        <span className="si-label">Reason (optional)</span>
        <input name="reason" className={field} maxLength={500} />
      </label>
      <Button type="submit" variant="primary" size="sm">
        Save
      </Button>
    </form>
  );
}

/** One queue for every human decision, ordered by how much the answer matters. */
export default async function ReviewPage() {
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) {
    return (
      <div className="flex flex-col gap-1">
        <h1 className="si-h1">Review</h1>
        <p className="m-0 text-ink-muted">Join or create a workspace to see its review queue.</p>
      </div>
    );
  }
  const ws = ctx.workspace.id;
  const isAdmin = atLeast(ctx.orgRole, 'Admin');
  const canDecide = isAdmin || atLeast(ctx.workspaceRole, 'Editor'); // identity and verdicts
  const canTriage = isAdmin || atLeast(ctx.workspaceRole, 'Operator'); // replies and meetings
  const cutoff = await overdueCutoff();

  const [identityRes, verdictRes, replyRes, meetingRes] = await Promise.all([
    supabase
      .from('review_item')
      .select(
        `id, match_method, match_confidence, created_at,
         mention:mention_id ( raw_name, raw_domain, raw_country, entity_type ),
         entity:candidate_entity ( display_name )`,
      )
      .eq('workspace_id', ws)
      .eq('status', 'pending')
      .order('match_confidence', { ascending: false })
      .limit(50),
    supabase
      .from('verdict_review')
      .select(
        `id, reason, claim_ids, created_at,
         verdict:verdict_id ( id, value, claim_ids,
           entity:entity_id ( display_name ),
           brief_version:brief_version_id ( brief:brief_id ( name ) ) )`,
      )
      .eq('workspace_id', ws)
      .eq('status', 'pending')
      .order('created_at')
      .limit(50),
    supabase
      .from('activity')
      .select(
        'id, occurred_at, payload, person:person_entity_id ( display_name ), account:account_id ( entity:entity_id ( display_name ) )',
      )
      .eq('workspace_id', ws)
      .eq('kind', 'reply_received')
      .is('payload->>reply_class', null)
      .lt('occurred_at', cutoff)
      .order('occurred_at')
      .limit(50),
    supabase
      .from('meeting')
      .select('id, scheduled_at, external_id, person:person_entity_id ( display_name )')
      .eq('workspace_id', ws)
      .is('account_id', null)
      .order('scheduled_at', { ascending: false })
      .limit(50),
  ]);

  const failed = [identityRes, verdictRes, replyRes, meetingRes].find((r) => r.error);
  const identity = (identityRes.data ?? []) as unknown as IdentityRow[];
  const verdictReviews = (verdictRes.data ?? []) as unknown as VerdictReviewRow[];
  const replies = (replyRes.data ?? []) as unknown as ReplyRow[];
  const meetings = (meetingRes.data ?? []) as unknown as MeetingRow[];

  // The claims each verdict question is about: the ones that disagree, or the ones it rests on.
  const claimIdsFor = (r: VerdictReviewRow) => (r.reason === 'conflict' ? r.claim_ids : (one(r.verdict)?.claim_ids ?? []));
  const claimIds = [...new Set(verdictReviews.flatMap(claimIdsFor))];
  const [claimRes, accountRes] = await Promise.all([
    claimIds.length
      ? supabase
          .from('claim')
          .select('id, value, quote, confidence, observed_at, predicate:predicate_id ( label, key ), source:source_record_id ( url )')
          .in('id', claimIds)
      : Promise.resolve({ data: [] }),
    meetings.length && canTriage
      ? supabase.from('account').select('id, entity:entity_id ( display_name )').eq('workspace_id', ws).limit(500)
      : Promise.resolve({ data: [] }),
  ]);
  const claimById = new Map(((claimRes.data ?? []) as unknown as ClaimRow[]).map((c) => [c.id, c]));
  const claimsFor = (r: VerdictReviewRow) => claimIdsFor(r).flatMap((id) => claimById.get(id) ?? []);
  const accounts = ((accountRes.data ?? []) as unknown as AccountRow[])
    .map((a) => ({ id: a.id, name: one(a.entity)?.display_name ?? a.id }))
    .sort((a, b) => a.name.localeCompare(b.name));

  const conflicts = verdictReviews.filter((r) => r.reason === 'conflict');
  const samples = verdictReviews.filter((r) => r.reason === 'sample');
  const total = identity.length + verdictReviews.length + replies.length + meetings.length;

  const verdictCard = (r: VerdictReviewRow) => {
    const v = one(r.verdict);
    if (!v) return null;
    return (
      <Card key={r.id}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[15px] font-semibold">{one(v.entity)?.display_name ?? 'Unknown entity'}</span>
          <VerdictChip value={v.value} />
          <span className="text-[13px] text-ink-muted">
            {one(one(v.brief_version)?.brief ?? null)?.name ?? 'Brief'} · waiting since {day(r.created_at)}
          </span>
        </div>
        <ClaimList claims={claimsFor(r)} />
        {canDecide ? <VerdictForm reviewId={r.id} current={v.value} /> : null}
      </Card>
    );
  };

  return (
    <>
      <div className="flex flex-col gap-1">
        <h1 className="si-h1">Review</h1>
        <p className="si-lede">
          Every decision that needs a person, most important first.
          {total > 0 ? ` ${total} waiting.` : ''}
        </p>
      </div>

      {failed?.error ? (
        <p role="alert" className="m-0 text-[13px] text-danger">
          Part of the queue could not be loaded: {failed.error.message}
        </p>
      ) : null}
      {total === 0 && !failed ? <p className="m-0 text-ink-muted">Nothing is waiting for review.</p> : null}

      <Section
        title="Conflicting claims"
        count={conflicts.length}
        hint="Sources disagree about this account. The verdict shown is the best reading of the evidence; confirm it or set the right one. Your decision holds until new claims arrive."
      >
        {conflicts.map(verdictCard)}
      </Section>

      <Section
        title="Identity matches"
        count={identity.length}
        hint="A mention that may be an existing company. Merge it into the candidate, create a new entity, or skip."
      >
        {identity.map((item) => {
          const mention = one(item.mention);
          const candidate = one(item.entity);
          return (
            <Card key={item.id}>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="flex flex-col gap-1">
                  <span className="si-label">Mention</span>
                  <span className="text-[14px] font-medium">{mention?.raw_name ?? '—'}</span>
                  <span className="text-[13px] text-ink-muted">
                    {[mention?.raw_domain, mention?.raw_country, mention?.entity_type].filter(Boolean).join(' · ') || '—'}
                  </span>
                </div>
                <div className="flex flex-col gap-1">
                  <span className="si-label">Candidate</span>
                  <span className="text-[14px] font-medium">{candidate?.display_name ?? 'None'}</span>
                  <span className="text-[13px] text-ink-muted">
                    {item.match_method ?? '—'} · {item.match_confidence != null ? `${item.match_confidence}% match` : '—'}
                  </span>
                </div>
              </div>
              {canDecide ? (
                <div className="flex flex-wrap items-end gap-2">
                  {candidate ? (
                    <form action={mergeAction.bind(null, item.id)}>
                      <Button type="submit" variant="primary" size="sm">
                        Merge
                      </Button>
                    </form>
                  ) : null}
                  <form action={createNewAction.bind(null, item.id)} className="flex items-end gap-2">
                    <input
                      name="new_name"
                      required
                      aria-label="New entity name"
                      placeholder="New entity name"
                      defaultValue={mention?.raw_name ?? ''}
                      className={field}
                    />
                    <Button type="submit" size="sm">
                      Create new
                    </Button>
                  </form>
                  <form action={skipAction.bind(null, item.id)}>
                    <Button type="submit" variant="quiet" size="sm">
                      Skip
                    </Button>
                  </form>
                </div>
              ) : null}
            </Card>
          );
        })}
      </Section>

      <Section
        title="Replies waiting over two working days"
        count={replies.length}
        hint="These replies have no class yet. A reply you classify here is never overwritten by automatic classification."
      >
        {replies.map((r) => {
          const snippet = typeof r.payload?.snippet === 'string' ? r.payload.snippet : typeof r.payload?.body === 'string' ? r.payload.body : null;
          return (
            <Card key={r.id}>
              <div className="flex flex-col gap-1">
                <span className="text-[14px] font-medium">
                  {one(r.person)?.display_name ?? 'Unknown person'}
                  {one(one(r.account)?.entity ?? null)?.display_name ? ` · ${one(one(r.account)?.entity ?? null)?.display_name}` : ''}
                </span>
                <span className="text-[13px] text-ink-muted">Received {day(r.occurred_at)}</span>
                {snippet ? <q className="text-[13px] text-ink-muted">{snippet.slice(0, 400)}</q> : null}
              </div>
              {canTriage ? (
                <form action={classifyOverdueReply.bind(null, r.id)} className="flex items-end gap-2">
                  <select name="reply_class" required defaultValue="" aria-label="Reply class" className={field}>
                    <option value="" disabled>
                      Choose a class…
                    </option>
                    {REPLY_CLASSES.map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                  <Button type="submit" variant="primary" size="sm">
                    Save
                  </Button>
                </form>
              ) : null}
            </Card>
          );
        })}
      </Section>

      <Section
        title="Unmatched meetings"
        count={meetings.length}
        hint="A meeting that could not be tied to an account by attendee or domain. Match it so it can be counted and attributed."
      >
        {meetings.map((m) => (
          <Card key={m.id}>
            <div className="flex flex-col gap-1">
              <span className="text-[14px] font-medium">Meeting on {day(m.scheduled_at)}</span>
              <span className="text-[13px] text-ink-muted">
                {[one(m.person)?.display_name, m.external_id].filter(Boolean).join(' · ') || 'No attendee details'}
              </span>
            </div>
            {canTriage ? (
              accounts.length === 0 ? (
                <p className="m-0 text-[13px] text-ink-muted">There are no accounts in this workspace to match it to yet.</p>
              ) : (
                <form action={matchMeeting.bind(null, m.id)} className="flex items-end gap-2">
                  <select name="account" required defaultValue="" aria-label="Account" className={field}>
                    <option value="" disabled>
                      Choose an account…
                    </option>
                    {accounts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                  <Button type="submit" variant="primary" size="sm">
                    Match
                  </Button>
                </form>
              )
            ) : null}
          </Card>
        ))}
      </Section>

      <Section
        title="Weekly verdict check"
        count={samples.length}
        hint="A sample of this week's new Confirmed verdicts. Your marks measure how accurate the verdicts are."
      >
        {samples.map(verdictCard)}
      </Section>
    </>
  );
}
