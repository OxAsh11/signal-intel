import { MetricTile } from '@signal/ui';
import { CardError } from './card-error';
import { type HomeCardProps, fmt, loadCard, usd } from './shared';

type Count = { count: number; href: string };
type Metrics = {
  accounts_found: Count;
  confirmed: Count;
  replies_month: Count;
  meetings_held_month: Count;
  spend_month: { spent_usd: number; cap_usd: number | null; pct: number | null; href: string };
};

const VERSION = 'home_metrics v1';

/**
 * The number tiles: accounts found (pool), Confirmed (mint), replies and meetings held this month
 * (grass) and spend against the monthly cap (lava). Each tile links to its rows.
 */
export async function Metrics(props: HomeCardProps) {
  const m = await loadCard<Metrics>('home_metrics', props);
  if (!m) return <CardError id="home-metrics" title="Key numbers" retry={props.retry} />;
  const spend = m.spend_month;
  return (
    <section aria-label="Key numbers" className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,160px),1fr))] gap-3">
      <MetricTile family="pool" label="Accounts found" value={fmt(m.accounts_found.count)} href={m.accounts_found.href} delta={props.brief ? 'judged under this Brief' : 'in this workspace'} version={VERSION} />
      <MetricTile family="mint" label="Confirmed" value={fmt(m.confirmed.count)} href={m.confirmed.href} delta="quoted evidence" version={VERSION} />
      <MetricTile family="grass" label="Replies" value={fmt(m.replies_month.count)} href={m.replies_month.href} delta="this month" version={VERSION} />
      <MetricTile family="grass" label="Meetings held" value={fmt(m.meetings_held_month.count)} href={m.meetings_held_month.href} delta="this month" version={VERSION} />
      <MetricTile
        family="lava"
        label="Spend"
        value={usd(spend.spent_usd)}
        href={spend.href}
        delta={spend.cap_usd == null ? 'this month · no cap' : `of ${usd(spend.cap_usd)} this month`}
        version={VERSION}
      />
    </section>
  );
}
