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
  // The hide lives on a wrapper with no si-* class: si-* rules are unlayered and would beat a
  // Tailwind utility on the same element (`.si-tabs { display: flex }` over `md:hidden`).
  return (
    <div className="min-w-0 md:hidden">
      <nav aria-label={`${label} pages`} className="si-tabs">
        {items.map((i) => (
          <Link key={i.key} href={i.href} className="si-tab" aria-current={i.href === pathname ? 'page' : undefined}>
            {i.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}
