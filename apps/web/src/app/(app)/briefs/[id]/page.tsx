import type { Metadata } from 'next';
import { Suspense } from 'react';
import { cookies } from 'next/headers';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext, atLeast } from '@/lib/org';
import { LENS_COOKIE, lensCookieValue } from '@/lib/brief-lens';
import { LensOnOpen } from '@/components/brief-lens';
import { BriefIntakeLoading } from './brief-intake-loading';
import { BriefEditor, StartPilotCard } from './brief-editor';
import { StateControls } from './state-controls';
import { Exclusions } from './exclusions';
import { RenameForm } from './rename-form';
import { PreviewPanel } from './preview-panel';
import type { WorkspacePredicate } from '@/lib/briefs';

export const metadata: Metadata = { title: 'Brief' };

export default async function BriefDetailPage({ params, searchParams }: PageProps<'/briefs/[id]'>) {
  const { id } = await params;
  const sp = await searchParams;
  const tab = (sp?.tab as string) || 'overview';
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;

  const { data: brief, error } = await supabase
    .from('brief').select('id, name, state, intake_status, intake_steps, draft_config, current_version_id, updated_at')
    .eq('id', id).eq('workspace_id', ctx.workspace.id).maybeSingle();

  if (error || !brief) notFound();
  // Opening a Brief makes it the lens (cookies can only be set from an action, so the page asks).
  const lensIsThis = lensCookieValue((await cookies()).get(LENS_COOKIE)?.value, ctx.workspace.id) === id;
  const canEdit = atLeast(ctx.workspaceRole, 'Editor');

  const hasDraftChanges = !!brief.draft_config;
  let seedConfig = brief.draft_config as import('@/lib/briefs').BriefConfig | null;
  if (!seedConfig && brief.current_version_id) {
    const { data: ver } = await supabase
      .from('brief_version').select('config').eq('id', brief.current_version_id).single();
    if (ver?.config) seedConfig = ver.config as import('@/lib/briefs').BriefConfig;
  }
  const briefForEditor = { ...brief, draft_config: seedConfig };
  // The workspace's own predicates (not global ones): the Columns editor fixes the type and fill
  // method of a key the workspace already collects, and a Brief without column specs starts its
  // editor from them.
  const { data: preds } = await supabase
    .from('predicate')
    .select('key, label, value_type, allowed_values, extraction_hint, decay_half_life_days, fill_method')
    .eq('workspace_id', ctx.workspace.id)
    .order('key');
  const workspacePredicates = (preds ?? []) as WorkspacePredicate[];
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
      {brief.state !== 'Archived' ? <LensOnOpen briefId={id} already={lensIsThis} /> : null}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-2">
          <Link href="/briefs" className="text-[13px] text-ink-muted">‹ Briefs</Link>
          <span className="si-eyebrow">{brief.state === 'Draft' ? 'New Brief · step 2 of 2' : `Brief · ${brief.state}`}</span>
          <RenameForm briefId={id} name={brief.name} canEdit={canEdit} />
          <p className="si-lede">{stateNote}</p>
        </div>
        {canEdit && brief.state !== 'Draft' ? <StateControls brief={brief} /> : null}
      </div>
      <nav aria-label="Brief tabs" className="flex flex-wrap gap-2 border-b border-line pb-2 text-[14px]">
        {['overview', 'icp', 'columns', 'sources', 'versions'].map((t) => (
          <Link
            key={t}
            href={`/briefs/${id}?tab=${t}`}
            className={tab === t ? 'font-semibold text-ink' : 'text-ink-muted hover:text-ink'}
          >
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </Link>
        ))}
        {brief.state === 'Pilot' ? (
          <>
            <Link href={`/briefs/${id}?tab=pilot`} className={tab === 'pilot' ? 'font-semibold text-ink' : 'text-ink-muted hover:text-ink'}>
              Pilot
            </Link>
            <Link href={`/briefs/${id}/pilot`} className="font-medium text-ink underline">Pilot results</Link>
          </>
        ) : null}
        <Link href={`/briefs/${id}/radars`} className="text-ink underline">Radars</Link>
        <Link href={`/briefs/${id}/versions`} className="text-ink underline">Version history</Link>
      </nav>

      <div className="si-cols">
        <div className="flex min-w-0 flex-col gap-4">
          {brief.intake_status === 'pending' ? (
            <BriefIntakeLoading briefId={id} steps={Array.isArray(brief.intake_steps) ? brief.intake_steps : []} />
          ) : (
            <BriefEditor brief={briefForEditor} canEdit={canEdit} workspacePredicates={workspacePredicates} />
          )}
          <details className="si-card">
            <summary className="cursor-pointer text-ink">
              <span className="font-medium">Exclusions</span>{' '}
              <span className="text-ink-muted">· companies and domains the engine skips. Open only to change them.</span>
            </summary>
            <div className="mt-4 flex flex-col gap-6">
              <Suspense fallback={null}>
                <Exclusions briefId={id} canEdit={canEdit} />
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
