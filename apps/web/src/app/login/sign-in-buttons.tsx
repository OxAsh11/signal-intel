'use client';

import { useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Button } from '@signal/ui';
import { createClient } from '@/lib/supabase/client';

type Provider = 'google' | 'azure';

export function SignInButtons() {
  const params = useSearchParams();
  const [pending, setPending] = useState<Provider | null>(null);
  const [error, setError] = useState<string | null>(params.get('error') ? 'Sign-in did not complete. Try again.' : null);
  const supabase = createClient();

  if (!supabase) {
    return (
      <p className="text-caution">
        Supabase is not configured. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY, then restart.
      </p>
    );
  }

  async function signIn(provider: Provider) {
    setPending(provider);
    setError(null);
    const next = params.get('next') ?? '/';
    const { error: err } = await supabase!.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
        scopes: provider === 'azure' ? 'email' : undefined,
      },
    });
    if (err) {
      setError(err.message);
      setPending(null);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <Button variant="primary" disabled={pending !== null} onClick={() => signIn('google')}>
        {pending === 'google' ? 'Redirecting…' : 'Continue with Google'}
      </Button>
      <Button disabled={pending !== null} onClick={() => signIn('azure')}>
        {pending === 'azure' ? 'Redirecting…' : 'Continue with Microsoft'}
      </Button>
      {error ? (
        <p role="alert" className="text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
