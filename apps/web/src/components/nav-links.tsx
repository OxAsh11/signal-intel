'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { cx } from '@signal/ui';
import { GROUP_LABELS, navPath, type NavLink } from '@/lib/nav';

type Counts = { review?: ReactNode; pinned?: ReactNode };

const ORDER = ['find', 'work', 'engage', 'measure'] as const;

function isOn(href: string, path: string): boolean {
  return href === '/' ? path === '/' : path === href || path.startsWith(`${href}/`);
}

export function NavCount({ n }: { n?: number }) {
  if (!n) return null;
  return (
    <span className="rounded-[6px] bg-marker-soft px-1.5 font-mono text-[12px] text-caution">
      {n}
      <span className="sr-only"> pending</span>
    </span>
  );
}

function Icon({ k }: { k: string }) {
  const cls = 'shrink-0 opacity-60';
  switch (k) {
    case 'home':
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" className={cls} aria-hidden="true">
          <rect x="1" y="1" width="5" height="5" rx="1" /><rect x="8" y="1" width="5" height="5" rx="1" />
          <rect x="1" y="8" width="5" height="5" rx="1" /><rect x="8" y="8" width="5" height="5" rx="1" />
        </svg>
      );
    case 'briefs':
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" className={cls} aria-hidden="true">
          <path d="M1 3h12M1 7h8M1 11h5" />
        </svg>
      );
    case 'sources':
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" className={cls} aria-hidden="true">
          <circle cx="7" cy="7" r="5.5" /><path d="M7 4v3l2 1.5" />
        </svg>
      );
    case 'accounts':
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" className={cls} aria-hidden="true">
          <rect x="1" y="2" width="12" height="10" rx="1.5" /><path d="M1 6h12M5 6v6M9 6v6" />
        </svg>
      );
    case 'people':
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" className={cls} aria-hidden="true">
          <circle cx="5" cy="5" r="2.5" /><path d="M1 12c0-2.2 1.8-4 4-4s4 1.8 4 4" />
          <circle cx="10.5" cy="4.5" r="2" /><path d="M9.5 12c.3-1.4 1.3-2.5 2.5-3" />
        </svg>
      );
    case 'review':
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" className={cls} aria-hidden="true">
          <circle cx="7" cy="7" r="5.5" />
          <path d="M7 6.5v3.5" strokeLinecap="round" strokeWidth="1.5" />
          <circle cx="7" cy="4.5" r="0.8" fill="currentColor" stroke="none" />
        </svg>
      );
    case 'email':
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" className={cls} aria-hidden="true">
          <rect x="1" y="3" width="12" height="8" rx="1.5" /><path d="M1 4.5l6 4 6-4" />
        </svg>
      );
    case 'crm':
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" className={cls} aria-hidden="true">
          <path d="M7 1.5l1.5 3.2 3.5.5-2.5 2.4.6 3.4L7 9.4l-3.1 1.6.6-3.4L2 5.2l3.5-.5z" />
        </svg>
      );
    case 'reports':
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" className={cls} aria-hidden="true">
          <path d="M1 11l3-4 3 2 3-5 3 3" />
        </svg>
      );
    case 'settings':
      return (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" className={cls} aria-hidden="true">
          <circle cx="7" cy="5" r="2.5" /><path d="M2 12c0-2.2 2.2-4 5-4s5 1.8 5 4" />
        </svg>
      );
    default:
      return null;
  }
}

export function NavLinks({ nav, counts, footer }: { nav: NavLink[]; counts: Counts; footer: ReactNode }) {
  const path = navPath(usePathname());
  const home = nav.filter((i) => i.group === 'home');
  const settings = nav.filter((i) => i.group === 'settings');

  const top = (item: NavLink) => {
    const childOn = item.children?.some((c) => isOn(c.href, path)) ?? false;
    const exact = item.children ? item.href === path : isOn(item.href, path);
    const on = exact || childOn;

    return (
      <div key={item.key} className="contents md:flex md:flex-col md:gap-0">
        <Link
          href={item.href}
          aria-current={exact ? 'page' : on ? 'true' : undefined}
          className={cx(
            'si-nav-item flex items-center gap-2 border-l-[3px] py-1.5 pr-2.5 pl-[9px] text-[13px] leading-5 whitespace-nowrap no-underline hover:no-underline rounded-r-[4px]',
            on
              ? 'si-nav-on border-signal bg-signal-soft font-medium text-signal-strong hover:bg-signal-soft'
              : 'border-transparent text-ink-muted hover:bg-surface-sunken hover:text-ink',
          )}
        >
          <Icon k={item.key} />
          <span className="flex-1 truncate">{item.label}</span>
          {item.key === 'review' ? counts.review : null}
          {item.placeholder ? (
            <span className="text-[11px] text-ink-faint">soon</span>
          ) : null}
        </Link>
        {item.key === 'accounts' && counts.pinned ? (
          <div className="hidden md:contents">{counts.pinned}</div>
        ) : null}
        {/* Sub-items only for non-placeholder items */}
        {!item.placeholder && item.children?.map((c) => (
          <Link
            key={c.key}
            href={c.href}
            aria-current={c.href === path ? 'page' : undefined}
            className={cx(
              'hidden rounded-r-[4px] border-l-[3px] py-1 pr-2.5 pl-[30px] text-[12px] leading-[18px] text-ink-muted no-underline hover:bg-surface-sunken hover:text-ink hover:no-underline md:block',
              c.href === path
                ? 'border-signal bg-signal-soft font-medium text-signal-strong hover:bg-signal-soft si-nav-on'
                : 'border-transparent',
            )}
          >
            {c.label}
          </Link>
        ))}
      </div>
    );
  };

  return (
    <nav
      aria-label="Main"
      className="relative flex min-w-0 gap-1 overflow-x-auto pr-3 pb-3 md:flex-1 md:flex-col md:gap-0 md:overflow-x-hidden md:overflow-y-auto md:pb-4"
    >
      {home.map(top)}
      {ORDER.map((g) => (
        <div key={g} role="group" aria-labelledby={`nav-group-${g}`} className="contents md:flex md:flex-col md:gap-0">
          <span id={`nav-group-${g}`} className="si-label hidden pl-3 pt-4 pb-1 md:block">
            {GROUP_LABELS[g]}
          </span>
          {nav.filter((i) => i.group === g).map(top)}
        </div>
      ))}
      <div className="contents md:mt-auto md:flex md:flex-col md:gap-1.5 md:border-t md:border-line md:pt-4">
        {settings.map(top)}
        {footer ? <div className="hidden md:block pr-3">{footer}</div> : null}
      </div>
    </nav>
  );
}
