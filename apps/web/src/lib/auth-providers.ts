import { cacheLife } from 'next/cache';
import { supabaseEnv } from './env';

export type OAuthProvider = 'google' | 'azure';
export type ProviderStatus = Record<OAuthProvider, boolean>;

/**
 * Which OAuth sign-in providers are switched on in the Supabase project, read from the public
 * GoTrue settings endpoint. A provider that is off gets a "not set up yet" placeholder instead of
 * a button that would fail. Cached for a few minutes, so switching one on shows up without a deploy.
 * Returns null when Supabase is unreachable or not configured.
 */
export async function getProviderStatus(): Promise<ProviderStatus | null> {
  'use cache';
  cacheLife('minutes');
  const env = supabaseEnv();
  if (!env) return null;
  try {
    const res = await fetch(`${env.url}/auth/v1/settings`, { headers: { apikey: env.anonKey } });
    if (!res.ok) return null;
    const body = (await res.json()) as { external?: Partial<Record<string, boolean>> };
    return { google: body.external?.google === true, azure: body.external?.azure === true };
  } catch {
    return null;
  }
}
