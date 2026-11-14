import type { Metadata } from 'next';
import { Suspense } from 'react';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext, atLeast } from '@/lib/org';
import { BriefIntakeLoading } from './brief-intake-loading';
import { BriefEditor, StartPilotCard } from './brief-editor';
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

  const stateNote =
    brief.state === 'Draft' && brief.intake_status === 'pending'
      ? 'The AI is drafting this Brief from your description.'
      : brief.state === 'Draft' && brief.intake_status === 'failed'
        ? 'The AI draft failed. Fill the parts in yourself, or start a new Brief.'
        : brief.state === 'Draft'
          ? 'Drafted by AI from your description. Check each part, then start a pilot.'
          : `This Brief is ${brief.state}.`;

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-2">
          <Link href="/briefs" className="text-[13px] text-ink-muted">‹ Briefs</Link>
          <span className="si-eyebrow">{brief.state === 'Draft' ? 'New Brief · step 2 of 2' : `Brief · ${brief.state}`}</span>
          <h1 className="si-h1">{brief.name}</h1>
          <p className="si-lede">{stateNote}</p>
        </div>
        {canEdit && brief.state !== 'Draft' ? <StateControls brief={brief} /> : null}
      </div>
      <nav aria-label="Brief" className="flex flex-wrap gap-4 text-[14px]">
        {brief.state === 'Pilot' ? <Link href={`/briefs/${id}/pilot`} className="font-medium text-ink underline">Pilot results</Link> : null}
        <Link href={`/briefs/${id}/radars`} className="text-ink underline">Radars</Link>
        <Link href={`/briefs/${id}/versions`} className="text-ink underline">Version history</Link>
      </nav>

      <div className="si-cols">
        <div className="flex min-w-0 flex-col gap-4">
          {brief.intake_status === 'pending' ? (
            <BriefIntakeLoading briefId={id} />
          ) : (
            <BriefEditor brief={briefForEditor} canEdit={canEdit} workspaceId={ctx.workspace.id} />
          )}
          <details className="si-card">
            <summary className="cursor-pointer text-ink">
              <span className="font-medium">Scoring and rules</span>{' '}
              <span className="text-ink-muted">· exclusions and the facts the AI extracts. Set for you; open only to change them.</span>
            </summary>
            <div className="mt-4 flex flex-col gap-6">
              <Suspense fallback={null}>
                <Exclusions briefId={id} canEdit={canEdit} />
              </Suspense>
              <Suspense fallback={null}>
                <Predicates briefId={id} workspaceId={ctx.workspace.id} canEdit={canEdit} />
              </Suspense>
            </div>
          </details>
        </div>

        <aside className="flex flex-col gap-4" aria-label="Pilot and changes">
          {brief.state === 'Draft' && brief.intake_status !== 'pending' ? (
            <StartPilotCard brief={briefForEditor} canEdit={canEdit} />
          ) : null}
          {showPreview ? (
            <Suspense fallback={null}>
              <PreviewPanel briefId={id} draft={brief.draft_config} nextVersion={nextVersion} />
            </Suspense>
          ) : null}
        </aside>
      </div>
    </>
  );
}
