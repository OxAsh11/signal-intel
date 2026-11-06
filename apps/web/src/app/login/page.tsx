import type { Metadata } from 'next';
import { Suspense } from 'react';
import { SignInButtons } from './sign-in-buttons';

export const metadata: Metadata = { title: 'Sign in' };

export default function LoginPage() {
  return (
    <main className="grid min-h-full place-items-center px-4 py-12">
      <div className="flex w-full max-w-sm flex-col gap-6 rounded-lg border border-line bg-surface p-8 shadow-[var(--shadow-sm)]">
        <div className="flex flex-col gap-1">
          <span className="text-[20px] leading-7 font-semibold tracking-tight">Signal Intel</span>
          <p className="text-ink-muted">Sign in with your work account. Your organisation&apos;s allowed domains apply.</p>
        </div>
        <Suspense fallback={null}>
          <SignInButtons />
        </Suspense>
      </div>
    </main>
  );
}
