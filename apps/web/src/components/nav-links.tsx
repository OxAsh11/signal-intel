'use client';

import Link from 'next/link';
import { Fragment } from 'react';
import { usePathname } from 'next/navigation';
import { cx } from '@signal/ui';

type NavScreen = { href: string; label: string; group: number };

/** Sidebar navigation in three groups: find and judge, work the accounts, reports and settings. */
export function NavLinks({ screens }: { screens: NavScreen[] }) {
  const pathname = usePathname();
  const ordered = [...screens].sort((a, b) => a.group - b.group);
  return (
    <nav aria-label="Screens" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:gap-0.5 md:overflow-visible">
      {ordered.map((s, i) => {
        const active = s.href === '/' ? pathname === '/' : pathname.startsWith(s.href);
        const newGroup = i > 0 && ordered[i - 1].group !== s.group;
        return (
          <Fragment key={s.href}>
            {newGroup ? <span aria-hidden="true" className="mx-2 my-2 hidden h-px bg-line md:block" /> : null}
            <Link
              href={s.href}
              aria-current={active ? 'page' : undefined}
              className={cx(
                'rounded-md px-3 py-2 whitespace-nowrap text-ink no-underline hover:bg-surface-sunken hover:no-underline',
                active && 'bg-surface-sunken font-medium',
              )}
            >
              {s.label}
            </Link>
          </Fragment>
        );
      })}
    </nav>
  );
}
