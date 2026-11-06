'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cx } from '@signal/ui';

export function NavLinks({ screens }: { screens: { href: string; label: string }[] }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Screens" className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:overflow-visible">
      {screens.map((s) => {
        const active = s.href === '/' ? pathname === '/' : pathname.startsWith(s.href);
        return (
          <Link
            key={s.href}
            href={s.href}
            aria-current={active ? 'page' : undefined}
            className={cx(
              'rounded-md px-3 py-1.5 whitespace-nowrap no-underline',
              active ? 'bg-signal-soft font-semibold text-signal' : 'text-ink-muted hover:bg-surface-sunken hover:text-ink',
            )}
          >
            {s.label}
          </Link>
        );
      })}
    </nav>
  );
}
