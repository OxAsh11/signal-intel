import Link from 'next/link';
import { Suspense, type ReactNode } from 'react';
import { SCREENS } from '@/lib/screens';
import { NavLinks } from './nav-links';
import { MembershipGate } from './membership-gate';
import { UserMenu } from './user-menu';
import { WorkspaceSwitcher } from './workspace-switcher';

/**
 * App shell on the light system: white sidebar and top bar with hairlines, and every screen inside
 * the adaptive page container (max 1440px, guideline margins). Under 768px the sidebar becomes a
 * scrolling row above the page.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-full grid-cols-1 bg-paper md:grid-cols-[236px_minmax(0,1fr)]">
      <aside className="border-b border-line bg-surface md:border-r md:border-b-0">
        <div className="flex h-16 items-center px-5">
          <Link href="/" className="inline-flex items-center gap-2.5 text-[17px] font-medium tracking-tight text-ink no-underline hover:no-underline">
            <span aria-hidden="true" className="si-brand-dot" />
            Signal Intel
          </Link>
        </div>
        {/* The nav reads the pathname, which is request data on dynamic routes. */}
        <Suspense fallback={null}>
          <NavLinks screens={SCREENS.map(({ href, label, group }) => ({ href, label, group }))} />
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
