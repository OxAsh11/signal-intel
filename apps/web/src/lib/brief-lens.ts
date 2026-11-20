import 'server-only';
import { cache } from 'react';
import { cookies } from 'next/headers';
import { getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';

/**
 * The Brief lens: which Brief the screens read, or `all`. One per workspace, kept in the
 * `si_brief` cookie as `<workspace>:<brief id | all>` so a value set in one workspace is ignored
 * in another.
 */
export const LENS_COOKIE = 'si_brief';
export const LENS_MAX_AGE = 60 * 60 * 24 * 90;

export type LensState = 'Draft' | 'Pilot' | 'Active' | 'Paused';
export type LensBrief = { id: string; name: string; state: LensState; publishedAt: string | null };
export type BriefLens = {
  briefId: string | null;
  briefName: string | null;
  source: 'url' | 'cookie' | 'latest' | 'all';
};

/**
 * The workspace's non-archived Briefs, by name, with when their current version was published
 * (null for a Draft that never was). Read under RLS, so only a workspace the caller can see.
 */
export const lensBriefs = cache(async (ws: string): Promise<LensBrief[]> => {
  const supabase = await createClient();
  if (!supabase) return [];
  const { data, error } = await supabase
    .from('brief')
    .select('id, name, state, current_version_id')
    .eq('workspace_id', ws)
    .neq('state', 'Archived')
    .order('name');
  if (error) {
    console.error(`brief lens: briefs failed for workspace ${ws}: ${error.message}`);
    return [];
  }
  const rows = (data ?? []) as { id: string; name: string; state: LensState; current_version_id: string | null }[];
  const versionIds = rows.map((r) => r.current_version_id).filter((v): v is string => v != null);
  const published = new Map<string, string>();
  if (versionIds.length) {
    const { data: versions, error: vErr } = await supabase.from('brief_version').select('id, published_at').in('id', versionIds);
    if (vErr) console.error(`brief lens: versions failed for workspace ${ws}: ${vErr.message}`);
    for (const v of (versions ?? []) as { id: string; published_at: string }[]) published.set(v.id, v.published_at);
  }
  return rows.map((r) => ({
    id: r.id,
    name: r.name,
    state: r.state,
    publishedAt: r.current_version_id ? (published.get(r.current_version_id) ?? null) : null,
  }));
});

/** The `si_brief` cookie's value for this workspace, or null when it was set in another one. */
export function lensCookieValue(raw: string | undefined, ws: string): string | null {
  if (!raw) return null;
  const i = raw.indexOf(':');
  if (i < 0 || raw.slice(0, i) !== ws) return null;
  return raw.slice(i + 1) || null;
}

const first = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

/**
 * Resolves the lens for the current workspace:
 *   1. `?brief=` in the URL (a Brief id or `all`);
 *   2. the `si_brief` cookie, if it was set in this workspace and that Brief is still in it;
 *   3. the most recently published Pilot or Active Brief;
 *   4. `all`.
 * A value that names an archived, deleted or other workspace's Brief is skipped silently.
 * Reads cookies and the session, so callers render inside <Suspense>.
 */
export async function getBriefLens(
  searchParams?: Record<string, string | string[] | undefined>,
): Promise<BriefLens> {
  const ctx = await getOrgContext();
  const ws = ctx?.workspace?.id;
  if (!ws) return { briefId: null, briefName: null, source: 'all' };
  const briefs = await lensBriefs(ws);
  const pick = (value: string | null | undefined): Omit<BriefLens, 'source'> | null => {
    if (!value) return null;
    if (value === 'all') return { briefId: null, briefName: null };
    const b = briefs.find((x) => x.id === value);
    return b ? { briefId: b.id, briefName: b.name } : null;
  };

  const fromUrl = pick(first(searchParams?.brief));
  if (fromUrl) return { ...fromUrl, source: 'url' };

  const fromCookie = pick(lensCookieValue((await cookies()).get(LENS_COOKIE)?.value, ws));
  if (fromCookie) return { ...fromCookie, source: 'cookie' };

  const latest = briefs
    .filter((b) => (b.state === 'Pilot' || b.state === 'Active') && b.publishedAt)
    .sort((a, b) => Date.parse(b.publishedAt!) - Date.parse(a.publishedAt!))[0];
  if (latest) return { briefId: latest.id, briefName: latest.name, source: 'latest' };

  return { briefId: null, briefName: null, source: 'all' };
}
