import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext, atLeast } from '@/lib/org';
import { BriefIntakeLoading } from './brief-intake-loading';
import { BriefEditor } from './brief-editor';
import { StateControls } from './state-controls';

export const metadata: Metadata = { title: 'Brief' };

export default async function BriefDetailPage({ params }: PageProps<'/briefs/[id]'>) {
  const { id } = await params;
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;

  const { data: brief, error } = await supabase
    .from('brief').select('id, name, state, intake_status, draft_config, current_version_id, updated_at')
    .eq('id', id).eq('workspace_id', ctx.workspace.id).maybeSingle();

  if (error || !brief) notFound();
  const canEdit = atLeast(ctx.workspaceRole, 'Editor');

  return (
    <>
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="m-0 text-[28px] leading-[34px] font-semibold tracking-[-0.015em]">{brief.name}</h1>
          <p className="m-0 text-sm text-ink-muted">
            {brief.state}
            {brief.state === 'Draft' && brief.intake_status === 'pending' && ' · AI is drafting…'}
            {brief.state === 'Draft' && brief.intake_status === 'failed' && ' · Draft failed — edit manually or retry'}
          </p>
        </div>
        {canEdit && <StateControls brief={brief} />}
      </div>

      {brief.intake_status === 'pending'
        ? <BriefIntakeLoading briefId={id} />
        : <BriefEditor brief={brief} canEdit={canEdit} workspaceId={ctx.workspace.id} />}

      {/* exclusions and predicates added by Task 7 */}
    </>
  );
}
