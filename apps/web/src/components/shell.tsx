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
  if (error) console.error(`sidebar counts failed for workspace ${ws}: ${error.message}`);
  return <NavCount n={Number((data as { review?: number } | null)?.review ?? 0)} />;
}

/** The Brief lens for the current workspace: the cookie or the latest Brief (the URL is read client-side). */
async function SidebarLens() {
  const ctx = await getOrgContext();
  if (!ctx?.workspace) return null;
  const [lens, briefs] = await Promise.all([getBriefLens(), lensBriefs(ctx.workspace.id)]);
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
        {/* Phone: brand and lens share one row. Desktop: the lens sits under the brand. */}
        <div className="flex shrink-0 items-center justify-between gap-3 py-3 pr-3 pl-5 md:block md:p-0">
          <div className="flex shrink-0 items-center md:h-16 md:px-5">
            <Link href="/" className="inline-flex items-center gap-2.5 text-[17px] font-medium tracking-tight text-ink no-underline hover:no-underline">
              <span aria-hidden="true" className="si-brand-dot" />
              Signal Intel
            </Link>
          </div>
          <div className="w-[52%] min-w-0 md:w-auto md:px-3 md:pb-3">
            <Suspense fallback={null}>
              <SidebarLens />
            </Suspense>
          </div>
        </div>
        {/* The nav reads the pathname, which is request data; the review count streams in after it. */}
        <Suspense fallback={null}>
          <NavLinks
            nav={navLinks()}
            counts={{ review: <Suspense fallback={null}><ReviewCount /></Suspense> }}
            footer={<Suspense fallback={null}><BudgetMeter /></Suspense>}
          />
        </Suspense>
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-10 flex h-16 items-center justify-between gap-3 border-b border-line bg-surface px-4 md:px-10">
          <Suspense fallback={<span className="si-label">Workspace</span>}>
            <WorkspaceSwitcher />
          </Suspense>
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
