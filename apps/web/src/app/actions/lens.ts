'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { refresh } from 'next/cache';
import { LENS_COOKIE, LENS_MAX_AGE, lensBriefs } from '@/lib/brief-lens';
import { getOrgContext } from '@/lib/org';

/**
 * Sets the Brief lens for the current workspace: `brief` is a Brief id or `all`. A Brief that is
 * not a non-archived Brief of this workspace (or a failed read of the Briefs) is refused: the
 * cookie is left as it was, the page re-renders in place and `{ ok: false }` tells the selector
 * to show the server's value again. Accepted, `next`, when given, is the page to land on (the
 * current one without a `?brief=` that would override the choice); otherwise the current page
 * re-renders in place.
 */
export async function setBriefLens(form: FormData): Promise<{ ok: boolean }> {
  const ctx = await getOrgContext();
  const ws = ctx?.workspace?.id;
  const brief = String(form.get('brief') ?? '').trim();
  let ok = false;
  if (ws && brief) {
    const briefs = brief === 'all' ? [] : await lensBriefs(ws);
    ok = brief === 'all' || (briefs?.some((b) => b.id === brief) ?? false);
    if (ok) {
      (await cookies()).set(LENS_COOKIE, `${ws}:${brief}`, {
        httpOnly: true,
        sameSite: 'lax',
        path: '/',
        maxAge: LENS_MAX_AGE,
      });
    }
  }
  if (!ok) {
    refresh();
    return { ok: false };
  }
  const next = sameSitePath(String(form.get('next') ?? ''));
  if (next) redirect(next);
  refresh();
  return { ok: true };
}

/**
 * `next` as a path on this site, or null. Parsed the way a browser would (which drops tabs and
 * newlines and reads `\` as `/`), so `/\t/evil.com` or `/\evil.com` can't pass as a local path;
 * and the normalised result must still start with exactly one `/`.
 */
function sameSitePath(next: string): string | null {
  if (!next.startsWith('/')) return null;
  try {
    const url = new URL(next, 'http://x');
    if (url.origin !== 'http://x') return null;
    // Guard the value that is sent, not only the parse: `/.//evil.com` parses on this origin but
    // normalises to `//evil.com`, which a browser reads as protocol-relative.
    const path = url.pathname + url.search;
    return path.startsWith('/') && !path.startsWith('//') && !path.startsWith('/\\') ? path : null;
  } catch {
    return null;
  }
}
