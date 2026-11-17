import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { atLeast, getOrgContext } from '@/lib/org';
import { CsvUploadForm } from './csv-upload-form';

export const metadata: Metadata = { title: 'Upload a list' };

interface Props {
  params: Promise<{ id: string }>;
}

export default async function CsvUploadPage({ params }: Props) {
  const { id: briefId } = await params;
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;
  if (!atLeast(ctx.workspaceRole, 'Editor')) redirect(`/briefs/${briefId}`);

  const { data: brief } = await supabase
    .from('brief')
    .select('id, name')
    .eq('id', briefId)
    .eq('workspace_id', ctx.workspace.id)
    .maybeSingle();

  if (!brief) notFound();

  return (
    <div className="mx-auto flex w-full max-w-[760px] flex-col gap-7">
      <div className="flex flex-col gap-2">
        <Link href={`/briefs/${briefId}/radars`} className="text-[13px] text-ink-muted">‹ Radars</Link>
        <span className="si-eyebrow">{brief.name}</span>
        <h1 className="si-h1">Upload a list</h1>
        <p className="si-lede">
          A CSV of companies, articles or event exhibitors becomes a radar. Every row is judged against this Brief like any
          other source.
        </p>
      </div>
      <CsvUploadForm briefId={briefId} workspaceId={ctx.workspace.id} />
    </div>
  );
}
