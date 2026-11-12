import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { atLeast, getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';
import { AutoRefresh } from '../auto-refresh';
import { MarkForm } from './mark-form';

export const metadata: Metadata = { title: 'Pilot' };

type Quote = { claim_id?: string; quote?: string | null };
type Row = {
  entityId: string;
  slot: number;
  name: string;
  verdictId: string | null;
  verdict: string | null;
  quotes: Quote[];
  band: string | null;
  priority: number | null;
  mark: 'confirm' | 'correct' | null;
};

/**
 * Calibrate (M1 step 4): the pilot's sample with verdicts, scores and evidence. Each mark is
 * stored as feedback; accuracy is the share of marked verdicts that were right.
 */
export default async function PilotPage({ params }: PageProps<'/briefs/[id]/pilot'>) {
  const { id } = await params;
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;

  const { data: brief } = await supabase
    .from('brief')
    .select('id, name, state, current_version_id')
    .eq('id', id)
    .eq('workspace_id', ctx.workspace.id)
    .maybeSingle();
  if (!brief) notFound();
  const version = brief.current_version_id as string | null;
  const canEdit = atLeast(ctx.workspaceRole, 'Editor');

  const [settingsRes, sampleRes, verdictRes, scoreRes] = await Promise.all([
    supabase
      .from('workspace_settings')
      .select('pilot_sample_size, pilot_accuracy_target_pct, pilot_min_confirmed_pct')
      .eq('workspace_id', ctx.workspace.id)
      .maybeSingle(),
    supabase
      .from('pilot_sample')
      .select('entity_id, slot, entity:entity_id ( display_name )')
      .eq('brief_version_id', version ?? '')
      .order('slot'),
    supabase
      .from('verdict')
      .select('id, entity_id, value, quotes')
      .eq('brief_version_id', version ?? ''),
    supabase
      .from('score')
      .select('band, priority, account:account_id ( entity_id )')
      .eq('brief_version_id', version ?? ''),
  ]);

  const settings = settingsRes.data ?? {
    pilot_sample_size: 20,
    pilot_accuracy_target_pct: 80,
    pilot_min_confirmed_pct: 20,
  };
  const verdicts = new Map((verdictRes.data ?? []).map((v) => [v.entity_id as string, v]));
  const scores = new Map(
    (scoreRes.data ?? []).map((s) => {
      const account = Array.isArray(s.account) ? s.account[0] : s.account;
      return [(account as { entity_id?: string } | null)?.entity_id ?? '', s];
    }),
  );

  const verdictIds = [...verdicts.values()].map((v) => v.id as string);
  const { data: feedback } = verdictIds.length
    ? await supabase
        .from('feedback')
        .select('target_id, action, created_at')
        .eq('target_type', 'verdict')
        .in('target_id', verdictIds)
        .order('created_at', { ascending: true })
    : { data: [] as { target_id: string; action: string }[] };
  const latestMark = new Map<string, 'confirm' | 'correct'>();
  for (const f of feedback ?? []) latestMark.set(f.target_id, f.action as 'confirm' | 'correct');

  const rows: Row[] = (sampleRes.data ?? []).map((s) => {
    const entity = Array.isArray(s.entity) ? s.entity[0] : s.entity;
    const v = verdicts.get(s.entity_id as string);
    const sc = scores.get(s.entity_id as string);
    return {
      entityId: s.entity_id as string,
      slot: s.slot as number,
      name: (entity as { display_name?: string } | null)?.display_name ?? 'Unknown account',
      verdictId: (v?.id as string) ?? null,
      verdict: (v?.value as string) ?? null,
      quotes: ((v?.quotes as Quote[]) ?? []).filter((q) => q.quote),
      band: (sc?.band as string) ?? null,
      priority: sc?.priority != null ? Number(sc.priority) : null,
      mark: v ? (latestMark.get(v.id as string) ?? null) : null,
    };
  });

  const marked = rows.filter((r) => r.mark).length;
  const right = rows.filter((r) => r.mark === 'confirm').length;
  const accuracy = marked ? Math.round((right / marked) * 100) : null;
  const judged = rows.filter((r) => r.verdict).length;
  const confirmed = rows.filter((r) => r.verdict === 'Confirmed').length;
  const confirmedPct = judged ? Math.round((confirmed / judged) * 100) : null;
  const filling = brief.state === 'Pilot' && rows.length < settings.pilot_sample_size;

  return (
    <>
      <div className="flex flex-col gap-1">
        <Link href={`/briefs/${id}`} className="text-sm text-ink-muted hover:text-ink">
          ← {brief.name}
        </Link>
        <h1 className="m-0 text-[28px] leading-[34px] font-semibold tracking-[-0.015em]">
          Pilot results
        </h1>
        <p className="m-0 max-w-[65ch] text-ink-muted">
          Mark each verdict right or wrong. Your marks measure accuracy and can teach the Brief new
          examples. Go live from the Brief page when you are satisfied.
        </p>
      </div>

      {brief.state !== 'Pilot' ? (
        <p className="m-0 text-ink-muted">This Brief is {brief.state}, not in a pilot.</p>
      ) : null}

      <div className="flex flex-wrap gap-6 text-[14px]">
        <span>
          Sample {rows.length} of {settings.pilot_sample_size}
        </span>
        <span>
          {accuracy == null
            ? 'No verdicts marked yet'
            : `Accuracy ${accuracy}% of ${marked} marked · target ${settings.pilot_accuracy_target_pct}%`}
        </span>
        {confirmedPct != null ? <span>Confirmed {confirmedPct}%</span> : null}
      </div>

      {accuracy != null && accuracy < settings.pilot_accuracy_target_pct ? (
        <p className="m-0 text-[13px] text-caution">
          Accuracy is below target. Correct the wrong verdicts and add them as examples, then
          preview and publish the changes before going live.
        </p>
      ) : null}
      {confirmedPct != null && judged >= 5 && confirmedPct < settings.pilot_min_confirmed_pct ? (
        <p className="m-0 text-[13px] text-caution">
          Few Confirmed accounts in the pilot ({confirmedPct}%). This industry may leave little
          public trace.
        </p>
      ) : null}

      {filling ? (
        <>
          <AutoRefresh everyMs={10000} />
          <p className="m-0 text-[13px] text-ink-muted">
            The pilot is still collecting accounts. This page updates as verdicts arrive.
          </p>
        </>
      ) : null}

      {rows.length === 0 ? (
        <p className="m-0 text-ink-muted">No accounts in the sample yet.</p>
      ) : (
        <div className="flex flex-col gap-3">
          {rows.map((r) => (
            <div
              key={r.entityId}
              className="flex flex-col gap-2 rounded-md border border-line bg-surface p-4"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[15px] font-semibold">{r.name}</span>
                {r.verdict ? (
                  <span className={`si-chip si-verdict-${r.verdict.toLowerCase()}`}>
                    {r.verdict}
                  </span>
                ) : (
                  <span className="text-[13px] text-ink-muted">Judging…</span>
                )}
                {r.band ? (
                  <span className="text-[13px] text-ink-muted">
                    {r.band}
                    {r.priority != null ? ` · ${Math.round(r.priority)}` : ''}
                  </span>
                ) : null}
                {r.mark ? (
                  <span className="text-[13px] text-ink-muted">
                    Marked {r.mark === 'confirm' ? 'right' : 'wrong'}
                  </span>
                ) : null}
              </div>
              {r.quotes.length ? (
                <ul className="m-0 pl-4 text-[13px] text-ink-muted">
                  {r.quotes.slice(0, 3).map((q, i) => (
                    <li key={q.claim_id ?? i}>
                      <q>{q.quote}</q>
                    </li>
                  ))}
                </ul>
              ) : null}
              {canEdit && brief.state === 'Pilot' && r.verdictId && r.verdict ? (
                <MarkForm verdictId={r.verdictId} current={r.verdict} />
              ) : null}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
