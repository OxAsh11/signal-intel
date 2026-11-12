import { createClient } from '@/lib/supabase/server';
import { publishBrief, requestPreview, requestPublishApproval } from '@/app/actions/briefs';
import { ActionForm } from './action-form';
import { AutoRefresh } from './auto-refresh';

type Change = { account_id: string; name: string | null; from: string; to: string };
type Result = {
  accounts?: number;
  changed_fields?: string[];
  totals?: { entering: number; leaving: number; band_changes: number };
  entering?: Change[];
  leaving?: Change[];
  band_changes?: Change[];
  counts?: { rejudge_verdicts?: number; reextract_records?: number; new_radar_types?: number };
};
type Preview = {
  id: string;
  status: 'pending' | 'ready' | 'failed';
  draft_config: unknown;
  tier: 'Narrow' | 'Redefine' | 'Expand' | null;
  result: Result;
  estimate_usd: number | null;
  duration_minutes: number | null;
  over_budget: boolean | null;
};

const TIER_HINT: Record<string, string> = {
  Narrow: 'Recomputes scores on existing facts. Instant, free and reversible.',
  Redefine: 'Re-reads existing evidence with AI. Takes minutes and costs AI calls.',
  Expand: 'Collects new evidence. Takes hours and real spend.',
};

/** Stable JSON so the stored draft and the current draft compare equal regardless of key order. */
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${stable(v)}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function duration(minutes: number | null): string {
  if (!minutes) return 'under a minute';
  if (minutes < 60) return `about ${minutes} min`;
  return `about ${Math.round(minutes / 60)} h`;
}

function ChangeList({ title, items, total }: { title: string; items: Change[]; total: number }) {
  if (total === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <span className="si-label">
        {title} ({total})
      </span>
      <ul className="m-0 pl-4 text-[13px]">
        {items.slice(0, 10).map((c) => (
          <li key={c.account_id}>
            {c.name ?? 'Unknown'} · {c.from} → {c.to}
          </li>
        ))}
        {total > 10 ? <li className="text-ink-muted">and {total - 10} more</li> : null}
      </ul>
    </div>
  );
}

/**
 * No edit of a published Brief applies without a preview (M1): tier, accounts entering or
 * leaving, band changes, cost and duration. Over budget, publishing waits for an approval (S6).
 */
export async function PreviewPanel({
  briefId,
  draft,
  nextVersion,
}: {
  briefId: string;
  draft: unknown;
  nextVersion: number;
}) {
  const supabase = await createClient();
  if (!supabase) return null;
  const { data } = await supabase
    .from('brief_preview')
    .select('id, status, draft_config, tier, result, estimate_usd, duration_minutes, over_budget')
    .eq('brief_id', briefId)
    .order('created_at', { ascending: false })
    .limit(1)
    .maybeSingle();
  const preview = data as Preview | null;
  const current = preview && stable(preview.draft_config) === stable(draft) ? preview : null;

  const shell = (children: React.ReactNode) => (
    <section className="flex flex-col gap-3 rounded-md border border-line bg-surface p-4">
      <h2 className="m-0 text-[17px] font-semibold">Unpublished changes</h2>
      {children}
    </section>
  );

  if (!current || current.status === 'failed') {
    return shell(
      <>
        <p className="m-0 text-[13px] text-ink-muted">
          {current?.status === 'failed'
            ? 'The preview could not be worked out. Try again.'
            : 'See what these changes would do to your accounts, and what they would cost, before they apply.'}
        </p>
        <div>
          <ActionForm action={requestPreview} fields={{ brief: briefId }} label="Preview changes" />
        </div>
      </>,
    );
  }

  if (current.status === 'pending') {
    return shell(
      <>
        <AutoRefresh />
        <p className="m-0 text-[13px] text-ink-muted">Working out the preview…</p>
      </>,
    );
  }

  let approval: string | null = null;
  if (current.over_budget) {
    const { data: rows } = await supabase
      .from('budget_approval')
      .select('status')
      .eq('job_type', 'brief.publish')
      .eq('job_args->>preview_id', current.id)
      .order('requested_at', { ascending: false })
      .limit(1);
    approval = (rows?.[0]?.status as string | undefined) ?? null;
  }

  const r = current.result ?? {};
  const totals = r.totals ?? { entering: 0, leaving: 0, band_changes: 0 };
  const cost = Number(current.estimate_usd ?? 0);
  return shell(
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span className="si-chip">{current.tier ?? 'No change'}</span>
        <span className="text-[13px] text-ink-muted">{current.tier ? TIER_HINT[current.tier] : ''}</span>
      </div>
      <p className="m-0 text-[14px]">
        {totals.entering} entering · {totals.leaving} leaving · {totals.band_changes} changing band
        {' '}(of {r.accounts ?? 0} scored accounts)
      </p>
      <p className="m-0 text-[14px]">
        Estimated cost ${cost.toFixed(2)} · {duration(current.duration_minutes)}
        {r.counts?.rejudge_verdicts ? ` · ${r.counts.rejudge_verdicts} verdicts re-judged` : ''}
        {r.counts?.reextract_records ? ` · ${r.counts.reextract_records} records re-read` : ''}
        {r.counts?.new_radar_types ? ` · ${r.counts.new_radar_types} radar types collecting` : ''}
      </p>
      <ChangeList title="Entering" items={r.entering ?? []} total={totals.entering} />
      <ChangeList title="Leaving" items={r.leaving ?? []} total={totals.leaving} />
      <ChangeList title="Band changes" items={r.band_changes ?? []} total={totals.band_changes} />
      {current.over_budget && approval !== 'approved' ? (
        approval === 'pending' ? (
          <p className="m-0 text-[13px] text-ink-muted">Over budget: waiting for an Owner or Admin to approve.</p>
        ) : (
          <div className="flex flex-col gap-1">
            <p className="m-0 text-[13px] text-caution">This edit is over budget and needs an approval.</p>
            <div>
              <ActionForm action={requestPublishApproval} fields={{ preview: current.id }} label="Request approval" />
            </div>
          </div>
        )
      ) : (
        <div>
          <ActionForm action={publishBrief} fields={{ brief: briefId }} label={`Publish version ${nextVersion}`} />
        </div>
      )}
    </>,
  );
}
