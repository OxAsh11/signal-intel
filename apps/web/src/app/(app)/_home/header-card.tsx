import Link from 'next/link';
import { type HomeCardProps, day, fmt, loadCard, plural } from './shared';

type Header = {
  lens: 'brief' | 'all';
  workspace_name: string;
  brief: null | { id: string; name: string; state: string; version: number | null; live_since: string | null; href: string };
  brief_count: number;
  sources_running: number;
  href: string;
};

/**
 * The lens under the H1: the Brief's name, version, state, live since and sources running; for
 * all Briefs, the workspace name and how many Briefs are in Pilot or Active.
 */
export async function HeaderCard(props: HomeCardProps) {
  const h = await loadCard<Header>('home_header', props);
  if (!h) {
    return (
      <p className="m-0 text-[13px] text-ink-body" data-card-error="">
        This card could not load.{' '}
        <a href={props.retry} className="text-ink underline">Retry</a>
      </p>
    );
  }
  const sources = (
    <Link href="/sources" className="text-ink-muted underline-offset-2 hover:underline">
      {fmt(h.sources_running)} {plural(h.sources_running, 'source', 'sources')} running
    </Link>
  );
  const parts = h.brief
    ? [
        h.brief.version == null ? 'Not published yet' : `Brief v${h.brief.version}`,
        h.brief.state,
        h.brief.live_since ? `Live since ${day(h.brief.live_since)}` : null,
        sources,
      ]
    : [
        'All Briefs',
        <Link key="briefs" href="/briefs" className="text-ink-muted underline-offset-2 hover:underline">
          {fmt(h.brief_count)} Pilot or Active {plural(h.brief_count, 'Brief', 'Briefs')}
        </Link>,
        sources,
      ];
  return (
    <div className="flex min-w-0 flex-col gap-1" data-home="header">
      <Link href={h.href} className="text-[20px] leading-7 font-medium text-ink no-underline hover:underline">
        {h.brief ? h.brief.name : h.workspace_name}
      </Link>
      <p className="m-0 flex flex-wrap gap-x-1.5 text-[13px] leading-[18px] text-ink-muted">
        {parts
          .filter((p) => p != null)
          .map((p, i) => (
            <span key={i}>
              {i > 0 ? <span aria-hidden="true">· </span> : null}
              {p}
            </span>
          ))}
      </p>
    </div>
  );
}
