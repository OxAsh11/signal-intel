import { createClient } from '@/lib/supabase/server';
import { saveSegment } from '@/app/actions/segments';

export async function SegmentsSection({ ws }: { ws: string }) {
  const supabase = await createClient();
  const { data: segments } = supabase
    ? await supabase.from('segment').select('id, name').eq('workspace_id', ws).order('name')
    : { data: [] };
  return (
    <section className="si-card">
      <h2 className="si-h2">Segments</h2>
      <ul className="m-0 pl-5 text-[14px]">
        {(segments ?? []).map((s: { id: string; name: string }) => (
          <li key={s.id}>{s.name}</li>
        ))}
        {!(segments ?? []).length ? <li className="text-ink-muted">No segments yet.</li> : null}
      </ul>
      <form action={saveSegment} className="mt-3 flex flex-col gap-2">
        <input type="hidden" name="workspace" value={ws} />
        <input name="name" required placeholder="segment name" className="rounded-sm border border-line-strong bg-surface px-2 py-1 text-[13px]" />
        <input name="filter" placeholder='{"wg":1,"ag":5}' className="rounded-sm border border-line-strong bg-surface px-2 py-1 text-[13px]" />
        <button type="submit" className="si-btn si-btn-sm self-start">Add segment</button>
      </form>
    </section>
  );
}