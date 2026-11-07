import Link from 'next/link';
import { Suspense, type ReactNode } from 'react';
import { SCREENS } from '@/lib/screens';
import { NavLinks } from './nav-links';
import { MembershipGate } from './membership-gate';
import { UserMenu } from './user-menu';
import { WorkspaceSwitcher } from './workspace-switcher';

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-full grid-cols-1 md:grid-cols-[232px_1fr]">
      <aside className="border-b border-line bg-surface md:border-r md:border-b-0">
        <div className="flex h-14 items-center px-4">
          <Link href="/" className="text-[15px] font-semibold tracking-tight text-ink no-underline">
            Signal Intel
          </Link>
        </div>
        <NavLinks screens={SCREENS.map(({ href, label }) => ({ href, label }))} />
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-10 flex h-14 items-center justify-between gap-3 border-b border-line bg-paper/90 px-4 backdrop-blur md:px-8">
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
        <main className="flex min-w-0 flex-col gap-6 px-4 py-6 md:px-8">{children}</main>
      </div>
    </div>
  );
}
