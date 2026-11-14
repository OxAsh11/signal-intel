'use client';

import { useState, type FormEvent } from 'react';
import { useSearchParams } from 'next/navigation';
import { Button } from '@signal/ui';
import { inputClass } from '@/components/forms';
import { createClient } from '@/lib/supabase/client';

type State =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'sent'; email: string }
  | { kind: 'error'; message: string };

/**
 * Email sign-in link (Supabase OTP). The link returns to /auth/callback, which exchanges the code
 * for a session. Supabase uses PKCE here, so the link only works in the browser that requested it.
 */
export function EmailLinkForm() {
  const params = useSearchParams();
  const [state, setState] = useState<State>(
    params.get('error')
      ? { kind: 'error', message: 'Sign-in did not complete. Request a new link and try again.' }
      : { kind: 'idle' },
  );
  const supabase = createClient();

  if (!supabase) {
    return (
      <p className="text-caution">
        Supabase is not configured. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY,
        then restart.
      </p>
    );
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const email = String(new FormData(event.currentTarget).get('email') ?? '').trim();
    if (!email) return;
    setState({ kind: 'sending' });
    const next = params.get('next') ?? '/';
    const { error } = await supabase!.auth.signInWithOtp({
      email,
      options: {
        emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
      },
    });
    setState(error ? { kind: 'error', message: error.message } : { kind: 'sent', email });
  }

  if (state.kind === 'sent') {
    return (
      <div role="status" className="flex flex-col gap-2">
        <p className="m-0">
          Check your inbox. We sent a sign-in link to <strong>{state.email}</strong>.
        </p>
        <p className="m-0 text-ink-muted">
          Open the link in this same browser. It expires in one hour.
        </p>
        <Button onClick={() => setState({ kind: 'idle' })}>Use a different email</Button>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3">
      <label className="flex flex-col gap-1">
        <span>Work email</span>
        <input
          name="email"
          type="email"
          required
          autoComplete="email"
          className={inputClass}
          placeholder="name@company.com"
        />
      </label>
      <Button type="submit" variant="primary" disabled={state.kind === 'sending'}>
        {state.kind === 'sending' ? 'Sending…' : 'Email me a sign-in link'}
      </Button>
      {state.kind === 'error' ? (
        <p role="alert" className="text-danger">
          {state.message}
        </p>
      ) : null}
    </form>
  );
}
