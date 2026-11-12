import { createClient } from '@/lib/supabase/server';
import { PredicateAddForm } from './predicate-add-form';

type Predicate = {
  id: string;
  key: string;
  label: string;
  value_type: string;
  allowed_values: unknown;
  extraction_hint: string | null;
};

export async function Predicates({
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
            <li key={pred.id}
              className="flex items-center gap-3 rounded border border-line px-3 py-2 text-sm">
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

      {canEdit && <PredicateAddForm workspaceId={workspaceId} />}
    </section>
  );
}
