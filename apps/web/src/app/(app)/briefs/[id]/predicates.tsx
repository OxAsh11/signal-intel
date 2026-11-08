import { createClient } from '@/lib/supabase/server';
import { createWorkspacePredicate } from '@/app/actions/briefs';

async function doCreate(fd: FormData): Promise<void> {
  'use server';
  await createWorkspacePredicate({}, fd);
}

type Predicate = {
  id: string;
  key: string;
  label: string;
  value_type: string;
  allowed_values: unknown;
  extraction_hint: string | null;
};

export async function Predicates({
  briefId: _briefId,
  workspaceId,
  canEdit,
}: {
  briefId: string;
  workspaceId: string;
  canEdit: boolean;
}) {
  const supabase = await createClient();
  if (!supabase) return null;

  const { data } = await supabase
    .from('predicate')
    .select('id, key, label, value_type, allowed_values, extraction_hint')
    .eq('workspace_id', workspaceId)
    .order('created_at', { ascending: true });

  const rows = (data ?? []) as Predicate[];

  return (
    <section className="flex flex-col gap-3 pt-4">
      <h2 className="text-base font-semibold">Columns (Predicates)</h2>

      {rows.length === 0 ? (
        <p className="text-sm text-ink-muted">No columns defined yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map(pred => (
            <li
              key={pred.id}
              className="flex items-center gap-3 rounded border border-line px-3 py-2 text-sm"
            >
              <span className="font-mono text-xs">{pred.key}</span>
              <span className="font-medium">{pred.label}</span>
              <span className="text-xs text-ink-muted">{pred.value_type}</span>
              {Array.isArray(pred.allowed_values) && pred.allowed_values.length > 0 && (
                <span className="text-xs text-ink-muted">
                  [{(pred.allowed_values as string[]).join(', ')}]
                </span>
              )}
              {pred.extraction_hint && (
                <span className="italic text-xs text-ink-muted">{pred.extraction_hint}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      {canEdit && (
        <form action={doCreate} className="flex flex-col gap-2">
          <input type="hidden" name="workspace" value={workspaceId} />
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">Key</span>
              <input
                name="key"
                required
                placeholder="e.g. employees"
                className="rounded border border-line bg-surface px-2 py-1 font-mono text-sm"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">Label</span>
              <input
                name="label"
                required
                placeholder="e.g. Employee count"
                className="rounded border border-line bg-surface px-2 py-1 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">Type</span>
              <select
                name="value_type"
                required
                className="rounded border border-line bg-surface px-2 py-1 text-sm"
              >
                <option value="text">text</option>
                <option value="number">number</option>
                <option value="boolean">boolean</option>
                <option value="enum">enum</option>
                <option value="date">date</option>
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">Allowed values (enum, JSON)</span>
              <input
                name="allowed_values"
                placeholder='["a","b"]'
                className="rounded border border-line bg-surface px-2 py-1 font-mono text-sm"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">Extraction hint</span>
              <input
                name="extraction_hint"
                placeholder="How to detect this…"
                className="rounded border border-line bg-surface px-2 py-1 text-sm"
              />
            </label>
            <button
              type="submit"
              className="rounded bg-accent px-3 py-1 text-sm font-medium text-white hover:bg-accent/90"
            >
              Add column
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
