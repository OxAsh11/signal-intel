import Link from 'next/link';
import { Card, FamilyTag } from '@signal/ui';
import { CardError } from './card-error';
import { type HomeCardProps, fmt, loadCard, plural, usd } from './shared';

type Count = { count: number; href: string };
type NeedsYou = {
  review: Count;
  replies_to_classify: Count;
  stale: Count & { cells: number | null; estimate_usd: number | string | null };
  errored: Count;
};

/** What waits for a person (mustard): Review decisions, replies to classify, stale and errored accounts. */
export async function NeedsYouCard(props: HomeCardProps) {
  const n = await loadCard<NeedsYou>('home_needs_you', props);
  if (!n) return <CardError id="home-needs" title="Needs you" family="mustard" retry={props.retry} />;
  // The count is accounts; the re-run is the cells of those accounts a run can reach (see the RPC).
  const s = n.stale;
  const staleNote =
    s.count === 0
      ? null
      : s.estimate_usd == null || s.cells == null
        ? ' · estimate unavailable'
        : Number(s.cells) === 0
          ? ' · nothing to re-run now'
          : ` · re-run ${fmt(s.cells)} ${plural(s.cells, 'cell', 'cells')} ≈ ${usd(s.estimate_usd)}`;
  const rows = [
    { key: 'review', c: n.review, text: plural(n.review.count, 'decision in Review', 'decisions in Review'), to: 'Review' },
    { key: 'replies', c: n.replies_to_classify, text: plural(n.replies_to_classify.count, 'reply to classify', 'replies to classify'), to: 'Email engine › Inbox' },
    { key: 'stale', c: n.stale, text: `${plural(n.stale.count, 'account', 'accounts')} stale${staleNote ?? ''}`, to: 'Accounts › Stale' },
    { key: 'errored', c: n.errored, text: `${plural(n.errored.count, 'account', 'accounts')} with errored cells`, to: 'Accounts › Errored' },
  ];
  const total = rows.reduce((a, r) => a + Number(r.c.count), 0);
  return (
    <Card family="mustard" aria-labelledby="home-needs">
      <div className="flex items-center justify-between gap-2">
        <h2 id="home-needs" className="si-h2">Needs you</h2>
        <FamilyTag family="mustard">{total === 0 ? 'Nothing waiting' : `${fmt(total)} waiting`}</FamilyTag>
      </div>
      <ul className="m-0 list-none p-0">
        {rows.map((r, i) => (
          <li key={r.key} className={i ? 'border-t border-line' : undefined}>
            <Link
              href={r.c.href}
              data-need={r.key}
              className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-2.5 text-ink no-underline hover:no-underline"
            >
              <span>
                <b className="font-mono font-medium">{fmt(r.c.count)}</b> {r.text}
              </span>
              <span className="text-[13px] text-signal">{r.to}</span>
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}
