import { createClient } from '@/lib/supabase/server';
import { setBusinessContext, generateBusinessContext } from '@/app/actions/business';

export async function BusinessSection({ ws }: { ws: string }) {
  const supabase = await createClient();
  const { data } = supabase ? await supabase.from('workspace_settings').select('business_context').eq('workspace_id', ws).maybeSingle() : { data: null };
  const ctx = (data as { business_context?: { lines?: string[] } } | null)?.business_context;
  const initial = (ctx?.lines ?? []).join('\n');
  return (
    <section className="si-card">
      <h2 className="si-h2">Business context</h2>
      <p className="m-0 text-[13px] text-ink-muted">Company name, offer, proof points (at most 5 lines). Fed to every AI prompt.</p>
      <form action={setBusinessContext} className="mt-3 flex flex-col gap-2">
        <input type="hidden" name="workspace" value={ws} />
        <textarea name="context" defaultValue={initial} rows={6} className="w-full rounded-sm border border-line-strong bg-surface px-3 py-2 text-[14px]" />
        <button type="submit" className="si-btn si-btn-sm self-start">Save</button>
      </form>
      <form action={generateBusinessContext} className="mt-3 flex items-center gap-2">
        <input type="hidden" name="workspace" value={ws} />
        <input name="domain" placeholder="example.com" className="rounded-sm border border-line-strong bg-surface px-2 py-1 text-[13px]" />
        <button type="submit" className="si-btn si-btn-sm">Generate from domain</button>
      </form>
    </section>
  );
}