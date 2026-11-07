import type { Metadata } from 'next';
import { Suspense } from 'react';
import { MembersAndAccess } from './members';

export const metadata: Metadata = { title: 'Settings' };

export default function SettingsPage() {
  return (
    <>
      <div className="flex flex-col gap-1">
        <h1 className="m-0 text-[28px] leading-[34px] font-semibold tracking-[-0.015em]">Settings</h1>
        <p className="m-0 max-w-[65ch] text-ink-muted">
          Members and access. Integrations and access modes, budgets, API tokens and health arrive with their modules in
          Phases 1 to 3.
        </p>
      </div>
      <Suspense fallback={<p className="m-0 text-ink-muted">Loading members…</p>}>
        <MembersAndAccess />
      </Suspense>
    </>
  );
}
