import Link from 'next/link';
import { Suspense, type ReactNode } from 'react';
import { navLinks } from '@/lib/nav';
import { getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';
import { getBriefLens, lensBriefs } from '@/lib/brief-lens';
import { BriefLens } from './brief-lens';
import { BudgetMeter } from './budget-meter';
import { NavCount, NavLinks } from './nav-links';
import { MembershipGate } from './membership-gate';
import { SearchBox } from './search-box';
import { ThemeToggle } from './theme-toggle';
import { UserMenu } from './user-menu';
import { WorkspaceSwitcher } from './workspace-switcher';

/**
 * App shell on the light system: white sidebar and top bar with hairlines, and every screen inside
 * the adaptive page container (max 1440px, guideline margins). Under 768px the sidebar becomes a
 * scrolling row of top-level items above the page, with the Brief lens beside the brand.
 */

/**
 * Pending decisions in the current workspace (identity review items plus verdict reviews), from
 * `public.sidebar_counts`, as the mustard count on Review. Streams in on its own so the sidebar
 * never waits on the database.
 */
async function ReviewCount() {
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;
  const ws = ctx.workspace.id;
  const { data, error } = await supabase.rpc('sidebar_counts', { p_ws: ws });
  if (error) {
    console.error(`sidebar counts failed for workspace ${ws}: ${error.message}`);
    return (
      <span title="Couldn't load" className="font-mono text-[12px] text-ink-muted">
        —
      </span>
    );
  }
  return <NavCount n={Number((data as { review?: number } | null)?.review ?? 0)} />;
}

/**
 * The member's pinned views under Accounts, each with the number of accounts it matches under the
 * lens (`public.pinned_views`). Streams in on its own. A failed load shows a muted "—", never nothing.
 */
async function PinnedViews() {
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;
  const ws = ctx.workspace.id;
  const lens = await getBriefLens();
  const { data, error } = await supabase.rpc('pinned_views', { p_ws: ws, p_brief: lens.briefId });
  if (error) {
    console.error(`pinned views failed for workspace ${ws}: ${error.message}`);
    return (
      <span title="Couldn't load" className="py-1 pr-2.5 pl-[22px] font-mono text-[12px] text-ink-muted">
        —
      </span>
    );
  }
  const pins = (Array.isArray(data) ? data : []) as { ref: string; name: string; count: number | null; href: string }[];
  return pins.map((p) => (
    <Link
      key={p.ref}
      href={p.href}
      data-pinned-view=""
      className="flex items-center justify-between gap-2 rounded-[4px] py-1 pr-2.5 pl-[22px] text-[13px] leading-[18px] text-ink-muted no-underline hover:bg-surface-sunken hover:text-ink hover:no-underline"
    >
      <span className="truncate">{p.name}</span>
      {p.count === null ? (
        <span title="Couldn't load" className="font-mono text-[12px]">—</span>
      ) : p.ref === 'errored' || p.ref === 'stale' ? (
        <NavCount n={p.count} />
      ) : (
        <span className="font-mono text-[12px]">{p.count}</span>
      )}
    </Link>
  ));
}

/** The top-bar search: needs the workspace, so it renders only inside one. */
async function Search() {
  const ctx = await getOrgContext();
  return ctx?.workspace ? <SearchBox ws={ctx.workspace.id} /> : null;
}

/**
 * The Brief lens for the current workspace: the cookie or the latest Brief (the URL is read
 * client-side). Renders nothing when the Briefs cannot be read, rather than a selector that
 * claims there are none.
 */
async function SidebarLens() {
  const ctx = await getOrgContext();
  if (!ctx?.workspace) return null;
  const [lens, briefs] = await Promise.all([getBriefLens(), lensBriefs(ctx.workspace.id)]);
  if (!briefs || lens.source === 'error') return null;
  return (
    <BriefLens
      briefs={briefs.map(({ id, name, state }) => ({ id, name, state }))}
      value={lens.briefId ?? 'all'}
    />
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    // Phone: two rows, the sidebar sized to its content and the page taking the rest. Without the
    // explicit rows the grid stretches both auto rows to fill min-h-full, opening a gap under the
    // sidebar on short pages.
    <div className="grid min-h-full grid-cols-1 grid-rows-[auto_1fr] bg-paper md:grid-cols-[236px_minmax(0,1fr)] md:grid-rows-1">
      <aside
        aria-label="Sidebar"
        className="min-w-0 border-b border-line bg-surface md:sticky md:top-0 md:flex md:h-screen md:flex-col md:border-r md:border-b-0"
      >
        {/* Brand row */}
        <div className="flex shrink-0 items-center py-3 pr-3 pl-5 md:h-16 md:px-5 md:py-0">
          <Link href="/" className="inline-flex items-center gap-2.5 text-[17px] font-medium tracking-tight text-ink no-underline hover:no-underline">
            <span aria-hidden="true" className="si-brand-dot" />
            Signal Intel
          </Link>
        </div>
        {/* Brief lens: below the brand, above the nav, full-width on phone, indented on desktop. */}
        <div className="shrink-0 px-3 pb-2">
          <Suspense fallback={null}>
            <SidebarLens />
          </Suspense>
        </div>
        {/* The nav reads the pathname, which is request data; the review count streams in after it. */}
        <Suspense fallback={null}>
          <NavLinks
            nav={navLinks()}
            counts={{
              review: <Suspense fallback={null}><ReviewCount /></Suspense>,
              pinned: <Suspense fallback={null}><PinnedViews /></Suspense>,
            }}
            footer={<Suspense fallback={null}><BudgetMeter /></Suspense>}
          />
        </Suspense>
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-10 flex h-16 items-center justify-between gap-3 border-b border-line bg-surface px-4 md:px-10">
          <Suspense fallback={<span className="si-label">Workspace</span>}>
            <WorkspaceSwitcher />
          </Suspense>
          {/* Phone: only the icon takes space here until it is opened; desktop keeps the field. */}
          <div className="min-w-0 shrink-0 md:flex-1 md:max-w-[360px]">
            <Suspense fallback={null}>
              <Search />
            </Suspense>
          </div>
          <ThemeToggle />
          <Suspense fallback={<span className="text-ink-faint">…</span>}>
            <UserMenu />
          </Suspense>
          <Suspense fallback={null}>
            <MembershipGate />
          </Suspense>
        </header>
        <main className="si-page">
          {/* Screens read the session and the workspace, so they stream in behind the shell. */}
          <Suspense fallback={<p className="m-0 text-ink-muted">Loading…</p>}>{children}</Suspense>
        </main>
      </div>
    </div>
  );
}
