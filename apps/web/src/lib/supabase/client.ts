'use client';

import { createBrowserClient } from '@supabase/ssr';
import { supabaseEnv } from '@/lib/env';

export function createClient() {
  const env = supabaseEnv();
  if (!env) return null;
  return createBrowserClient(env.url, env.anonKey);
}
