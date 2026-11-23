import 'server-only';
import { cookies } from 'next/headers';
import { connection } from 'next/server';
import { createServerClient } from '@supabase/ssr';
import { supabaseEnv } from '@/lib/env';

/**
 * Supabase client acting as the signed-in user. Every query runs under RLS with the user's JWT.
 * Reads cookies(), so callers must sit inside a <Suspense> boundary (Cache Components).
 * `connection()` first: the auth client reads the clock (Date.now) as it starts, which Next.js
 * refuses during a prerender, so the client only ever runs at request time.
 */
export async function createClient() {
  const env = supabaseEnv();
  if (!env) return null;
  await connection();
  const cookieStore = await cookies();
  return createServerClient(env.url, env.anonKey, {
    // Every read here runs under the signed-in user's RLS. Opt out of the server fetch cache so a
    // write made in a Server Action is visible on the very next request (e.g. Settings order).
    global: { fetch: (input, init) => fetch(input, { ...init, cache: 'no-store' }) },
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll: (toSet) => {
        try {
          toSet.forEach(({ name, value, options }) => cookieStore.set(name, value, options));
        } catch {
          // Called from a Server Component: the proxy refreshes the session instead.
        }
      },
    },
  });
}

export type CurrentUser = { id: string; email: string | null; name: string | null };

/** Data Access Layer: the one place that reads the session. */
export async function getCurrentUser(): Promise<CurrentUser | null> {
  const supabase = await createClient();
  if (!supabase) return null;
  const { data } = await supabase.auth.getUser();
  if (!data.user) return null;
  const meta = data.user.user_metadata as { full_name?: string; name?: string } | undefined;
  return { id: data.user.id, email: data.user.email ?? null, name: meta?.full_name ?? meta?.name ?? null };
}
