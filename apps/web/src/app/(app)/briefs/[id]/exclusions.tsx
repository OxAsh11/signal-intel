import { createClient } from '@/lib/supabase/server';
import { removeExclusion } from '@/app/actions/briefs';
import { ExclusionAddForm } from './exclusion-add-form';

type Exclusion = {
  id: string;
  value_type: string;
  entity_id: string | null;
  pattern: string | null;
  reason: string | null;
};

async function doRemove(fd: FormData): Promise<void> {
  'use server';
  await removeExclusion({}, fd);
}

export async function Exclusions({ briefId, canEdit }: { briefId: string; canEdit: boolean }) {
  const supabase = await createClient();
  if (!supabase) return null;

  const { data } = await supabase
    .from('exclusion_list')
    .select('id, value_type, entity_id, pattern, reason')
    .eq('brief_id', briefId)
    .order('added_at', { ascending: true });

  const rows = (data ?? []) as Exclusion[];

  return (
    <section className="flex flex-col gap-3 pt-4">
      <h2 className="text-base font-semibold">Exclusions</h2>

      {rows.length === 0 ? (
        <p className="text-sm text-ink-muted">No exclusions added yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map(exc => (
            <li key={exc.id}
              className="flex items-center justify-between gap-2 rounded border border-line px-3 py-2 text-sm">
              <span>
                <span className="mr-2 font-mono text-xs text-ink-muted">{exc.value_type}</span>
                {exc.pattern ?? exc.entity_id}
                {exc.reason && <span className="ml-2 text-ink-muted">— {exc.reason}</span>}
              </span>
              {canEdit && (
                <form action={doRemove}>
                  <input type="hidden" name="id" value={exc.id} />
                  <button type="submit" className="text-xs text-danger hover:underline">Remove</button>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}

      {canEdit && <ExclusionAddForm briefId={briefId} />}
    </section>
  );
}
