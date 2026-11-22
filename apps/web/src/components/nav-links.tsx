'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { cx } from '@signal/ui';
import { GROUP_LABELS, navPath, type NavLink } from '@/lib/nav';

/** Counts shown beside an item: server-rendered nodes (see NavCount), so they can stream in. */
type Counts = { review?: ReactNode };

const ORDER = ['find', 'work', 'engage', 'measure'] as const;

/** The one highlighted row: the page the user is on (`si-nav-on` marks it for tests). */
const ACTIVE = 'si-nav-on bg-signal-soft font-medium text-signal-strong hover:bg-signal-soft';

/** A top-level item is active on its route or below it; Home only on `/`. */
function isOn(href: string, path: string): boolean {
  return href === '/' ? path === '/' : path === href || path.startsWith(`${href}/`);
}

/** A mustard count of pending decisions; nothing at zero. */
export function NavCount({ n }: { n?: number }) {
  if (!n) return null;
  return (
    <span className="rounded-[6px] bg-marker-soft px-1.5 font-mono text-[12px] text-caution">
      {n}
      <span className="sr-only"> pending</span>
    </span>
  );
}

/**
 * The sidebar in journey order: Home, then Find, Work, Engage and Measure, with Settings and the
 * footer slot (budget meter) at the bottom. Under 768px it is one scrolling row of top-level items:
 * group headings, sub-items and the footer slot are hidden (sub-items live in each page's tabs).
 */
export function NavLinks({ nav, counts, footer }: { nav: NavLink[]; counts: Counts; footer: ReactNode }) {
  const path = navPath(usePathname());
  const home = nav.filter((i) => i.group === 'home');
  const settings = nav.filter((i) => i.group === 'settings');

  const top = (item: NavLink) => {
    const childOn = item.children?.some((c) => c.href === path) ?? false;
    const exact = item.children ? item.href === path : isOn(item.href, path);
    const on = exact || childOn;
    return (
      <div key={item.key} className="contents md:flex md:flex-col md:gap-0.5">
        <Link
          href={item.href}
          aria-current={exact ? 'page' : on ? 'true' : undefined}
          className={cx(
            'flex items-center justify-between gap-2 rounded-[4px] px-2.5 py-1.5 text-[14px] leading-5 whitespace-nowrap text-ink no-underline hover:bg-surface-sunken hover:no-underline',
            exact && ACTIVE,
            // Holds the current page: on desktop the sub-item carries the highlight and the parent is
            // only medium weight; on phone sub-items are hidden, so the parent is highlighted.
            !exact && childOn && 'bg-signal-soft font-medium text-signal-strong md:bg-transparent md:text-ink md:hover:bg-surface-sunken',
          )}
        >
          <span>{item.label}</span>
          {item.key === 'review' ? counts.review : null}
        </Link>
        {item.children?.map((c) => (
          <Link
            key={c.key}
            href={c.href}
            aria-current={c.href === path ? 'page' : undefined}
            className={cx(
              'hidden rounded-[4px] py-1 pr-2.5 pl-[22px] text-[13px] leading-[18px] text-ink-muted no-underline hover:bg-surface-sunken hover:text-ink hover:no-underline md:block',
              c.href === path && ACTIVE,
            )}
          >
            {c.label}
          </Link>
        ))}
      </div>
    );
  };

  return (
    // relative: the phone row scrolls sideways, and it must be the containing block of the
    // absolutely positioned sr-only text in its counts, or that text widens the page.
    <nav
      aria-label="Main"
      className="relative flex min-w-0 gap-1 overflow-x-auto px-3 pb-3 md:flex-1 md:flex-col md:gap-0.5 md:overflow-x-hidden md:overflow-y-auto md:pb-4"
    >
      {home.map(top)}
      {ORDER.map((g) => (
        // A named group, so a screen reader announces "Find, group" before its items.
        <div key={g} role="group" aria-labelledby={`nav-group-${g}`} className="contents md:flex md:flex-col md:gap-0.5">
          <span id={`nav-group-${g}`} className="si-label hidden px-2.5 pt-4 pb-1 md:block">
            {GROUP_LABELS[g]}
          </span>
          {nav.filter((i) => i.group === g).map(top)}
        </div>
      ))}
      <div className="contents md:mt-auto md:flex md:flex-col md:gap-1.5 md:border-t md:border-line md:pt-4">
        {settings.map(top)}
        {footer ? <div className="hidden md:block">{footer}</div> : null}
      </div>
    </nav>
  );
}
