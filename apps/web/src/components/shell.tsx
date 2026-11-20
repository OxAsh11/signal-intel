import Link from 'next/link';
import { Suspense, type ReactNode } from 'react';
import { navLinks } from '@/lib/nav';
import { getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';
import { NavLinks } from './nav-links';
import { MembershipGate } from './membership-gate';
import { UserMenu } from './user-menu';
import { WorkspaceSwitcher } from './workspace-switcher';

/**
 * App shell on the light system: white sidebar and top bar with hairlines, and every screen inside
 * the adaptive page container (max 1440px, guideline margins). Under 768px the sidebar becomes a
 * scrolling row of top-level items above the page.
 */

/** Pending decisions in the current workspace: identity review items plus verdict reviews. */
async function reviewCount(): Promise<number> {
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return 0;
  const ws = ctx.workspace.id;
  const head = (table: 'review_item' | 'verdict_review') =>
    supabase.from(table).select('id', { count: 'exact', head: true }).eq('workspace_id', ws).eq('status', 'pending');
  const [items, verdicts] = await Promise.all([head('review_item'), head('verdict_review')]);
  return (items.count ?? 0) + (verdicts.count ?? 0);
}

async function Sidebar() {
  const review = await reviewCount();
  // footer: the budget meter slot (P4 Task 3).
  return <NavLinks nav={navLinks()} counts={{ review }} footer={null} />;
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-full grid-cols-1 bg-paper md:grid-cols-[236px_minmax(0,1fr)]">
      <aside className="min-w-0 border-b border-line bg-surface md:sticky md:top-0 md:flex md:h-screen md:flex-col md:border-r md:border-b-0">
        <div className="flex h-16 shrink-0 items-center px-5">
          <Link href="/" className="inline-flex items-center gap-2.5 text-[17px] font-medium tracking-tight text-ink no-underline hover:no-underline">
            <span aria-hidden="true" className="si-brand-dot" />
            Signal Intel
          </Link>
        </div>
        {/* The nav reads the pathname and the review count, which are request data. */}
        <Suspense fallback={null}>
          <Sidebar />
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
