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
