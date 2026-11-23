import { createClient } from '@/lib/supabase/server';
import { getOrgContext } from '@/lib/org';
import { saveTag, deleteTag } from '@/app/actions/tags';

export async function DataSection({ ws }: { ws: string }) {
  const supabase = await createClient();
  const ctx = await getOrgContext();
  const canEdit = (ctx?.workspaceRole ?? '') === 'Editor' || (ctx?.workspaceRole ?? '') === 'Admin' || (ctx?.workspaceRole ?? '') === 'Owner';
  const { data: tags } = supabase ? await supabase.from('account_tag').select('id, name, colour').eq('workspace_id', ws).order('name') : { data: [] };
  return (
    <section className="si-card">
      <h2 className="si-h2">Hand labels</h2>
      <ul className="m-0 flex flex-col gap-1 pl-5 text-[14px]">
        {(tags ?? []).map((t: { id: string; name: string; colour: string }) => (
          <li key={t.id} className="flex items-center gap-2">
            <span className="inline-block h-3 w-3 rounded-full" style={{ background: t.colour }} />
            {t.name}
            {canEdit ? (
              <form action={deleteTag} className="inline">
                <input type="hidden" name="tag" value={t.id} />
                <button type="submit" className="text-[12px] text-ink-muted underline">delete</button>
              </form>
            ) : null}
          </li>
        ))}
        {!(tags ?? []).length ? <li className="text-ink-muted">No labels yet.</li> : null}
      </ul>
      {canEdit ? (
        <form action={saveTag} className="mt-3 flex items-center gap-2">
          <input type="hidden" name="workspace" value={ws} />
          <input name="name" required placeholder="label name" className="rounded-sm border border-line-strong bg-surface px-2 py-1 text-[13px]" />
          <input name="colour" type="color" defaultValue="#6b7280" className="h-8 w-8 border-0 bg-transparent" />
          <button type="submit" className="si-btn si-btn-sm">Add label</button>
        </form>
      ) : null}
    </section>
  );
}