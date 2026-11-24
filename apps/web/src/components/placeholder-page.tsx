import Link from 'next/link';
import { Card } from '@signal/ui';
import { navLinks, navSection, type NavItem } from '@/lib/nav';
import { getOrgContext } from '@/lib/org';
import { SectionTabs } from './section-tabs';

const COMING: Record<NonNullable<NavItem['placeholder']>, string> = {
  email: 'Coming with the Email engine',
  crm: 'Coming with the CRM',
  p6: 'Coming in P6',
};

/**
 * An honest page for a menu item whose module has not shipped: its name, what it will do and when
 * it arrives. Never sample data. Where today's product already does the job on another screen, it
 * links there; an index page (Email engine, CRM) lists its sub-pages as cards.
 */
export async function PlaceholderPage({ item }: { item: NavItem }) {
  const ctx = await getOrgContext();
  const ws = ctx?.workspace?.id;
  const section = navSection(item.href);
  return (
    <>
      {section?.children ? <SectionTabs label={section.label} items={navLinks(section.children)} /> : null}
      <div className="flex flex-col gap-3">
        {item.placeholder && (
          <div className="flex items-center gap-3 rounded-lg border border-line bg-surface p-4">
            <span className="rounded-full bg-marker-soft px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-widest text-caution">
              Coming Soon
            </span>
            <span className="text-[13px] text-ink-muted">{COMING[item.placeholder]}</span>
          </div>
        )}
        <h1 className="si-h1">{item.label}</h1>
        <p className="si-lede">{item.job}</p>
        {item.todayHref && item.todayLabel && ws ? (
          <Link href={item.todayHref(ws)} className="self-start text-ink underline">
            {item.todayLabel}
          </Link>
        ) : null}
      </div>
      {item.children ? (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,260px),1fr))] gap-3">
          {item.children.map((c) => (
            <Card key={c.key} aria-labelledby={`card-${c.key}`}>
              <h2 id={`card-${c.key}`} className="si-h2">
                <Link href={c.href} className="text-ink no-underline hover:underline">{c.label}</Link>
              </h2>
              <p className="m-0 text-ink-body">{c.job}</p>
              {c.todayHref && c.todayLabel && ws ? (
                <Link href={c.todayHref(ws)} className="self-start text-[13px] text-ink underline">
                  {c.todayLabel}
                </Link>
              ) : null}
            </Card>
          ))}
        </div>
      ) : null}
    </>
  );
}
