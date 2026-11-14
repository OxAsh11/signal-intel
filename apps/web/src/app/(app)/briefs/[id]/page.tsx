import type { Metadata } from 'next';
import { Suspense } from 'react';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext, atLeast } from '@/lib/org';
import { BriefIntakeLoading } from './brief-intake-loading';
import { BriefEditor } from './brief-editor';
import { StateControls } from './state-controls';
import { Exclusions } from './exclusions';
import { Predicates } from './predicates';
import { PreviewPanel } from './preview-panel';

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

  const hasDraftChanges = !!brief.draft_config;
  let seedConfig = brief.draft_config as import('@/lib/briefs').BriefConfig | null;
  if (!seedConfig && brief.current_version_id) {
    const { data: ver } = await supabase
      .from('brief_version').select('config').eq('id', brief.current_version_id).single();
    if (ver?.config) seedConfig = ver.config as import('@/lib/briefs').BriefConfig;
  }
  const briefForEditor = { ...brief, draft_config: seedConfig };
  const showPreview = canEdit && hasDraftChanges && ['Pilot', 'Active', 'Paused'].includes(brief.state);
  let nextVersion = 1;
  if (showPreview) {
    const { data: last } = await supabase
      .from('brief_version').select('version').eq('brief_id', id)
      .order('version', { ascending: false }).limit(1).maybeSingle();
    nextVersion = (last?.version ?? 0) + 1;
  }

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
      <Link href={`/briefs/${id}/versions`} className="text-sm text-ink-muted hover:text-ink">
        Version history →
      </Link>
      <Link href={`/briefs/${id}/radars`} className="text-sm text-ink-muted hover:text-ink">
        Radars →
      </Link>
      {brief.state === 'Pilot' && (
        <Link href={`/briefs/${id}/pilot`} className="text-sm text-ink-muted hover:text-ink">
          Pilot results →
        </Link>
      )}

      {brief.intake_status === 'pending'
        ? <BriefIntakeLoading briefId={id} />
        : <BriefEditor brief={briefForEditor} canEdit={canEdit} workspaceId={ctx.workspace.id} />}

      {showPreview && (
        <Suspense fallback={null}>
          <PreviewPanel briefId={id} draft={brief.draft_config} nextVersion={nextVersion} />
        </Suspense>
      )}

      <Suspense fallback={null}>
        <Exclusions briefId={id} canEdit={canEdit} />
      </Suspense>
      <Suspense fallback={null}>
        <Predicates briefId={id} workspaceId={ctx.workspace.id} canEdit={canEdit} />
      </Suspense>
    </>
  );
}
