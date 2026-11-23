import { lensBriefs } from '@/lib/brief-lens';
import type { createClient } from '@/lib/supabase/server';

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Supabase = NonNullable<Awaited<ReturnType<typeof createClient>>>;

/** A saved view's predicate columns, as ids (a ref may be an id or a key). Null: the lens Brief's columns. */
export async function viewColumnIds(supabase: Supabase, ws: string, columns: unknown): Promise<string[] | null> {
  if (!Array.isArray(columns)) return null;
  const refs = columns
    .filter((c): c is { kind: string; ref: string } => c !== null && typeof c === 'object' && (c as { kind?: unknown }).kind === 'predicate' && typeof (c as { ref?: unknown }).ref === 'string')
    .map((c) => c.ref);
  if (refs.length === 0) return null;
  const keys = refs.filter((r) => !UUID.test(r));
  const byKey = new Map<string, string>();
  if (keys.length) {
    const { data } = await supabase.from('predicate').select('id, key, workspace_id').in('key', keys).or(`workspace_id.eq.${ws},workspace_id.is.null`);
    // A workspace column wins over a global one of the same key.
    for (const p of (data ?? []) as { id: string; key: string; workspace_id: string | null }[]) {
      if (p.workspace_id || !byKey.has(p.key)) byKey.set(p.key, p.id);
    }
  }
  const ids = refs.map((r) => (UUID.test(r) ? r : byKey.get(r))).filter((x): x is string => !!x);
  return ids.length ? ids : null;
}

/** A row of public.list_views. */
export type ViewDef = {
  id: string | null;
  preset: string | null;
  name: string;
  brief_id: string | null;
  filter: unknown;
  sort: unknown;
  columns: unknown;
  layout?: string | null;
  visibility: string;
};

export type ResolvedView =
  | { status: 'ok'; view: ViewDef | null; briefId: string | null }
  | { status: 'missing' }
  | { status: 'error'; code: string | undefined; message: string };

/**
 * The view `?view=` names and the Brief the table reads under it (ruling R10). A preset reads under
 * the lens. A saved view keeps its own Brief: one saved under Brief A opens under A whatever the lens
 * is (the caller puts it in the URL); one saved under all Briefs reads under the lens. 'missing' when
 * the view is gone, not visible to this member, or its Brief is archived, so the page says so instead
 * of silently showing every account.
 */
export async function resolveView(supabase: Supabase, ws: string, ref: string | undefined, lensBrief: string | null): Promise<ResolvedView> {
  if (!ref) return { status: 'ok', view: null, briefId: lensBrief };
  const saved = UUID.test(ref);
  // Every saved view the member can see, whatever its Brief; presets do not depend on the Brief.
  const { data, error } = await supabase.rpc('list_views', { p_ws: ws, p_brief: saved ? null : lensBrief });
  if (error) return { status: 'error', code: error.code, message: error.message };
  const rows = (data ?? []) as ViewDef[];
  const view = rows.find((v) => (saved ? v.id === ref : v.id == null && v.preset === ref)) ?? null;
  if (!view) return { status: 'missing' };
  if (!saved || !view.brief_id) return { status: 'ok', view, briefId: lensBrief };
  const briefs = await lensBriefs(ws);
  if (!briefs?.some((b) => b.id === view.brief_id)) return { status: 'missing' };
  return { status: 'ok', view, briefId: view.brief_id };
}
