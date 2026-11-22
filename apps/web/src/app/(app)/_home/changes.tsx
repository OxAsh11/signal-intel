import Link from 'next/link';
import type { ReactNode } from 'react';
import { CardError } from './card-error';
import { type HomeCardProps, day, fmt, loadCard, plural } from './shared';

type Item =
  | { kind: 'verdict'; at: string; name: string; from: string; to: string; quote: string | null; href: string | null }
  | { kind: 'first_verdicts'; at: string; brief_id: string; brief_name: string; day: string; count: number; href: string }
  | { kind: 'brief_version'; at: string; brief_id: string; brief_name: string; version: number; stale_cells: number; href: string }
  | { kind: 'new_accounts'; at: string; radar_id: string; radar_name: string; count: number; href: string };
type Changes = { items: Item[] };

const QUOTE_MAX = 140;
const clip = (s: string) => (s.length > QUOTE_MAX ? `${s.slice(0, QUOTE_MAX - 1).trimEnd()}…` : s);

/** "Today", "Yesterday" or "18 Nov", by UTC day. */
function when(iso: string, now: Date): string {
  const d = new Date(iso);
  const days = Math.round((Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) - Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())) / 864e5);
  return days <= 0 ? 'Today' : days === 1 ? 'Yesterday' : day(iso);
}

function line(item: Item): ReactNode {
  switch (item.kind) {
    case 'verdict': {
      const name = item.href ? (
        <Link href={item.href} className="font-medium text-ink no-underline hover:underline">{item.name}</Link>
      ) : (
        <span className="font-medium text-ink">{item.name}</span>
      );
      return (
        <>
          {name}: {item.from} → {item.to}
          {item.quote ? <span className="text-ink-muted"> · &quot;{clip(item.quote)}&quot;</span> : null}
        </>
      );
    }
    case 'first_verdicts':
      // One line per Brief per day, however many accounts its first run judged.
      return (
        <>
          <Link href={item.href} className="font-medium text-ink no-underline hover:underline">
            {fmt(item.count)} first {plural(item.count, 'verdict', 'verdicts')}
          </Link>{' '}
          under {item.brief_name}
        </>
      );
    case 'brief_version':
      return (
        <>
          <Link href={item.href} className="font-medium text-ink no-underline hover:underline">
            {item.brief_name} v{item.version}
          </Link>{' '}
          published ·{' '}
          <Link href="/accounts?view=stale" className="text-ink-muted">
            {fmt(item.stale_cells)} stale {plural(item.stale_cells, 'cell', 'cells')}
          </Link>
        </>
      );
    case 'new_accounts':
      return (
        <>
          <Link href={item.href} className="font-medium text-ink no-underline hover:underline">
            {fmt(item.count)} new {plural(item.count, 'account', 'accounts')}
          </Link>{' '}
          from {item.radar_name}
        </>
      );
  }
}

/**
 * The last 7 days, newest first: verdict changes with their quote, first verdicts (one line per
 * Brief per day), Brief versions, new accounts by radar.
 */
export async function ChangesCard(props: HomeCardProps) {
  const c = await loadCard<Changes>('home_changes', props);
  if (!c) return <CardError id="home-changes" title="What changed" retry={props.retry} />;
  const now = new Date();
  return (
    <section aria-labelledby="home-changes" className="si-card">
      <h2 id="home-changes" className="si-h2">What changed</h2>
      <p className="m-0 -mt-2 text-[12px] text-ink-muted">Last 7 days</p>
      {c.items.length === 0 ? (
        <p className="m-0 text-[13px] text-ink-muted">Nothing changed in the last 7 days.</p>
      ) : (
        <ul className="m-0 list-none p-0">
          {c.items.map((item, i) => (
            <li key={`${item.kind}-${item.at}-${i}`} data-change={item.kind} className="flex gap-3 border-t border-line py-2.5 text-[13px] leading-[19px] first:border-t-0 first:pt-0">
              <span className="min-w-[64px] font-mono text-[12px] text-ink-muted">{when(item.at, now)}</span>
              <span className="min-w-0 text-ink-body [overflow-wrap:anywhere]">{line(item)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
