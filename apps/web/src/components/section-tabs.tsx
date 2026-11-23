'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { NavLink } from '@/lib/nav';

/**
 * Phone only: the sub-pages of a menu (Email engine, CRM) as a scrolling tab row, since the
 * sidebar row under 768px shows top-level items only. On desktop the sidebar lists them.
 */
export function SectionTabs({ label, items }: { label: string; items: NavLink[] }) {
  const pathname = usePathname();
  return (
    <nav aria-label={`${label} pages`} className="si-tabs md:hidden">
      {items.map((i) => (
        <Link key={i.key} href={i.href} className="si-tab" aria-current={i.href === pathname ? 'page' : undefined}>
          {i.label}
        </Link>
      ))}
    </nav>
  );
}
