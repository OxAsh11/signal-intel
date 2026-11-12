import type { Metadata } from 'next';
import { Suspense } from 'react';
import { NotConfigured } from '@/components/not-configured';
import { getProviderStatus, type OAuthProvider } from '@/lib/auth-providers';
import { EmailLinkForm } from './email-link-form';
import { SignInButtons } from './sign-in-buttons';

export const metadata: Metadata = { title: 'Sign in' };

const PROVIDERS: { id: OAuthProvider; name: string; setup: string[] }[] = [
  {
    id: 'google',
    name: 'Google sign-in',
    setup: [
      'Google Cloud Console → Credentials → OAuth client ID (Web application).',
      'Redirect URI: <your Supabase URL>/auth/v1/callback.',
      'Supabase → Authentication → Sign In / Providers → Google: paste the client ID and secret.',
    ],
  },
  {
    id: 'azure',
    name: 'Microsoft sign-in',
    setup: [
      'Microsoft Entra → App registrations → New registration.',
      'Redirect URI: <your Supabase URL>/auth/v1/callback.',
      'Supabase → Authentication → Sign In / Providers → Azure: paste the client ID and secret.',
    ],
  },
];

async function SignInOptions() {
  const status = await getProviderStatus();
  // Unknown (Supabase unreachable): show every option; the provider reports its own error.
  const emailEnabled = status === null || status.email;
  const enabled = PROVIDERS.filter((p) => status === null || status[p.id]).map((p) => p.id);
  const missing = PROVIDERS.filter((p) => status !== null && !status[p.id]);
  return (
    <div className="flex flex-col gap-3">
      {emailEnabled ? <EmailLinkForm /> : null}
      {emailEnabled && enabled.length > 0 ? (
        <p className="m-0 text-center text-ink-muted" aria-hidden="true">
          or
        </p>
      ) : null}
      {enabled.length > 0 ? <SignInButtons providers={enabled} showUrlError={!emailEnabled} /> : null}
      {!emailEnabled && enabled.length === 0 ? (
        <p className="m-0 text-ink-muted">Sign-in isn&apos;t available yet. It appears here once a provider is switched on.</p>
      ) : null}
      {missing.map((p) => (
        <NotConfigured key={p.id} name={p.name} setup={p.setup} />
      ))}
    </div>
  );
}

export default function LoginPage() {
  return (
    <main className="grid min-h-full place-items-center px-4 py-12">
      <div className="flex w-full max-w-sm flex-col gap-6 rounded-lg border border-line bg-surface p-8 shadow-[var(--shadow-sm)]">
        <div className="flex flex-col gap-1">
          <span className="text-[20px] leading-7 font-semibold tracking-tight">Signal Intel</span>
          <p className="text-ink-muted">Sign in with your work account. Your organisation&apos;s allowed domains apply.</p>
        </div>
        <Suspense fallback={<p className="m-0 text-ink-muted">Loading sign-in options…</p>}>
          <SignInOptions />
        </Suspense>
      </div>
    </main>
  );
}
