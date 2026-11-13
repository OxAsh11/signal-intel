'use client';
import { useActionState } from 'react';
import { ChipField } from './chip-field';
import { startPilot } from '@/app/actions/briefs';
import { Button } from '@signal/ui';
import type { BriefConfig } from '@/lib/briefs';

type Brief = { id: string; state: string; draft_config: BriefConfig | null };

function isPublishable(cfg: BriefConfig | null): boolean {
  if (!cfg) return false;
  return !!(cfg.offer?.trim() && cfg.hypothesis?.trim() &&
    (cfg.examples?.positive?.length ?? 0) >= 3 &&
    (cfg.examples?.negative?.length ?? 0) >= 2 &&
    (cfg.sources?.radar_types?.length ?? 0) >= 1);
}

const RADAR_TYPES = ['news_publications', 'job_postings', 'public_registries', 'tenders_procurement', 'events', 'directories', 'public_social', 'company_websites'];

function lines(arr: string[] | undefined) { return (arr ?? []).join('\n'); }
function fromLines(s: string) { return s.split('\n').map(x => x.trim()).filter(Boolean); }

export function BriefEditor({ brief, canEdit, workspaceId: _workspaceId }: { brief: Brief; canEdit: boolean; workspaceId: string }) {
  const cfg: BriefConfig = brief.draft_config ?? {};
  const [pubState, pubFormAction, pubPending] = useActionState(startPilot, {});
  const canPublish = brief.state === 'Draft' && canEdit && isPublishable(cfg);
  const editorDisabled = !canEdit;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <ChipField briefId={brief.id} fieldName="offer" label="Offer" required disabled={editorDisabled}
          currentConfig={cfg as Record<string, unknown>}
          renderValue={c => <span className="line-clamp-1">{String(c.offer ?? '')}</span>}
          renderEditor={(c, onChange) => (
            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">What is being sold or offered?</span>
              <input defaultValue={String(c.offer ?? '')}
                onChange={e => onChange({ ...c, offer: e.target.value })}
                className="rounded border border-line bg-surface-raised px-3 py-2 text-sm" />
            </label>
          )} />
        <ChipField briefId={brief.id} fieldName="hypothesis" label="Hypothesis" required disabled={editorDisabled}
          currentConfig={cfg as Record<string, unknown>}
          renderValue={c => <span className="line-clamp-1">{String(c.hypothesis ?? '')}</span>}
          renderEditor={(c, onChange) => (
            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">The observable public fact that marks a target.</span>
              <textarea rows={3} defaultValue={String(c.hypothesis ?? '')}
                onChange={e => onChange({ ...c, hypothesis: e.target.value })}
                className="rounded border border-line bg-surface-raised px-3 py-2 text-sm resize-y" />
            </label>
          )} />
        <ChipField briefId={brief.id} fieldName="examples" label="Examples" required disabled={editorDisabled}
          currentConfig={cfg as Record<string, unknown>}
          renderValue={c => {
            const ex = c.examples as { positive?: string[]; negative?: string[] } | undefined;
            return <span>{ex?.positive?.length ?? 0} positive · {ex?.negative?.length ?? 0} negative</span>;
          }}
          renderEditor={(c, onChange) => {
            const ex = (c.examples as { positive?: string[]; negative?: string[] } | undefined) ?? {};
            return (
              <div className="flex flex-col gap-2">
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-ink-muted">Positive examples (≥ 3, one per line)</span>
                  <textarea rows={4} defaultValue={lines(ex.positive)}
                    onChange={e => onChange({ ...c, examples: { ...ex, positive: fromLines(e.target.value) } })}
                    className="rounded border border-line bg-surface-raised px-3 py-2 text-sm resize-y font-mono text-xs" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-ink-muted">Negative examples (≥ 2, one per line)</span>
                  <textarea rows={3} defaultValue={lines(ex.negative)}
                    onChange={e => onChange({ ...c, examples: { ...ex, negative: fromLines(e.target.value) } })}
                    className="rounded border border-line bg-surface-raised px-3 py-2 text-sm resize-y font-mono text-xs" />
                </label>
              </div>
            );
          }} />
        <ChipField briefId={brief.id} fieldName="sources" label="Sources" required disabled={editorDisabled}
          isEmpty={c => ((c.sources as { radar_types?: string[] } | undefined)?.radar_types?.length ?? 0) === 0}
          currentConfig={cfg as Record<string, unknown>}
          renderValue={c => <span>{((c.sources as { radar_types?: string[] } | undefined)?.radar_types ?? []).join(', ') || 'None'}</span>}
          renderEditor={(c, onChange) => {
            const current = ((c.sources as { radar_types?: string[] } | undefined)?.radar_types ?? []) as string[];
            return (
              <fieldset className="flex flex-col gap-1">
                <legend className="text-xs text-ink-muted mb-1">Select at least one radar type</legend>
                {RADAR_TYPES.map(t => (
                  <label key={t} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={current.includes(t)}
                      onChange={e => {
                        const next = e.target.checked ? [...current, t] : current.filter(x => x !== t);
                        onChange({ ...c, sources: { ...((c.sources as object) ?? {}), radar_types: next } });
                      }} />
                    {t.replace(/_/g, ' ')}
                  </label>
                ))}
              </fieldset>
            );
          }} />
        <ChipField briefId={brief.id} fieldName="icp" label="ICP" disabled={editorDisabled}
          currentConfig={cfg as Record<string, unknown>}
          renderValue={c => {
            const icp = c.icp as BriefConfig['icp'];
            const count = (icp?.verticals?.length ?? 0) + (icp?.geographies?.length ?? 0) + (icp?.size_bands?.length ?? 0) + (icp?.commercial_roles?.length ?? 0);
            return <span>{count} attributes</span>;
          }}
          renderEditor={(c, onChange) => {
            const icp = (c.icp as BriefConfig['icp'] | undefined) ?? {};
            return (
              <div className="flex flex-col gap-2">
                {(['verticals', 'geographies', 'size_bands', 'commercial_roles'] as const).map(field => (
                  <label key={field} className="flex flex-col gap-1">
                    <span className="text-xs text-ink-muted capitalize">{field.replace('_', ' ')} (one per line)</span>
                    <textarea rows={2} defaultValue={lines(icp[field])}
                      onChange={e => onChange({ ...c, icp: { ...icp, [field]: fromLines(e.target.value) } })}
                      className="rounded border border-line bg-surface-raised px-3 py-2 text-sm resize-y font-mono text-xs" />
                  </label>
                ))}
              </div>
            );
          }} />
        <ChipField briefId={brief.id} fieldName="personas" label="Personas" disabled={editorDisabled}
          currentConfig={cfg as Record<string, unknown>}
          renderValue={c => {
            const p = c.personas as BriefConfig['personas'];
            const count = (p?.buyer?.length ?? 0) + (p?.user?.length ?? 0) + (p?.influencer?.length ?? 0);
            return <span>{count} attributes</span>;
          }}
          renderEditor={(c, onChange) => {
            const p = (c.personas as BriefConfig['personas'] | undefined) ?? {};
            return (
              <div className="flex flex-col gap-2">
                {(['buyer', 'user', 'influencer'] as const).map(field => (
                  <label key={field} className="flex flex-col gap-1">
                    <span className="text-xs text-ink-muted capitalize">{field.replace('_', ' ')} (one per line)</span>
                    <textarea rows={2} defaultValue={lines(p[field])}
                      onChange={e => onChange({ ...c, personas: { ...p, [field]: fromLines(e.target.value) } })}
                      className="rounded border border-line bg-surface-raised px-3 py-2 text-sm resize-y font-mono text-xs" />
                  </label>
                ))}
              </div>
            );
          }} />
      </div>

      {brief.state === 'Draft' && canEdit && (
        <form action={pubFormAction} className="pt-2">
          <fieldset disabled={pubPending} className="contents">
            <input type="hidden" name="brief" value={brief.id} />
            <Button type="submit" variant="primary" disabled={!canPublish}>
              Start pilot
            </Button>
            <p className="mt-1 text-xs text-ink-muted">
              {canPublish
                ? 'The pilot scores a small sample of accounts so you can check the verdicts before going live.'
                : 'Fill in all required fields before starting the pilot.'}
            </p>
            {pubState?.error && <p className="text-xs text-danger mt-1">{pubState.error}</p>}
          </fieldset>
        </form>
      )}
    </div>
  );
}
