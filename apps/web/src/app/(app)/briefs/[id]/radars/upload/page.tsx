import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext } from '@/lib/org';
import { csvRadarFormAction } from '@/app/actions/radars';

export const metadata: Metadata = { title: 'Upload CSV Radar' };

interface Props {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function CsvUploadPage({ params }: Props) {
  const { id: briefId } = await params;
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;

  const { data: brief } = await supabase
    .from('brief')
    .select('id, name')
    .eq('id', briefId)
    .eq('workspace_id', ctx.workspace.id)
    .maybeSingle();

  if (!brief) notFound();

  return (
    <div>
      <h1 className="si-h1">Upload CSV Radar</h1>
      <p>Brief: {brief.name}</p>
      <form action={csvRadarFormAction}>
        <input type="hidden" name="brief" value={briefId} />
        <input type="hidden" name="workspace" value={ctx.workspace.id} />

        <div>
          <label htmlFor="name">Radar name</label>
          <input id="name" name="name" type="text" required placeholder="e.g. Prospect list" />
        </div>

        <div>
          <label htmlFor="file">CSV file</label>
          <input id="file" name="file" type="file" accept=".csv" required />
        </div>

        <button type="submit">Upload &amp; create radar</button>
      </form>
    </div>
  );
}
