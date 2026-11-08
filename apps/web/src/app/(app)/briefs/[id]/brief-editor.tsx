'use client';
import { useActionState } from 'react';
import { ChipField } from './chip-field';
import { publishBrief } from '@/app/actions/briefs';
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

const RADAR_TYPES = ['news_publications', 'job_postings', 'public_registries', 'tenders_procurement', 'events', 'directories', 'company_websites'];

export function BriefEditor({ brief, canEdit, workspaceId: _workspaceId }: { brief: Brief; canEdit: boolean; workspaceId: string }) {
  const cfg: BriefConfig = brief.draft_config ?? {};
  const [pubState, pubFormAction, pubPending] = useActionState(publishBrief, {});
  const canPublish = brief.state === 'Draft' && canEdit && isPublishable(cfg);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <ChipField briefId={brief.id} fieldName="offer" label="Offer" required
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
        <ChipField briefId={brief.id} fieldName="hypothesis" label="Hypothesis" required
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
        {/* Examples chip: renders two text-areas, one for positive (comma-separated) and one for negative */}
        <ChipField briefId={brief.id} fieldName="examples" label="Examples" required
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
                  <textarea rows={4} defaultValue={(ex.positive ?? []).join('\n')}
                    onChange={e => onChange({ ...c, examples: { ...ex, positive: e.target.value.split('\n').map(s => s.trim()).filter(Boolean) } })}
                    className="rounded border border-line bg-surface-raised px-3 py-2 text-sm resize-y font-mono text-xs" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-ink-muted">Negative examples (≥ 2, one per line)</span>
                  <textarea rows={3} defaultValue={(ex.negative ?? []).join('\n')}
                    onChange={e => onChange({ ...c, examples: { ...ex, negative: e.target.value.split('\n').map(s => s.trim()).filter(Boolean) } })}
                    className="rounded border border-line bg-surface-raised px-3 py-2 text-sm resize-y font-mono text-xs" />
                </label>
              </div>
            );
          }} />
        {/* Sources chip */}
        <ChipField briefId={brief.id} fieldName="sources" label="Sources" required
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
      </div>

      {brief.state === 'Draft' && canEdit && (
        <form action={pubFormAction} className="pt-2">
          <fieldset disabled={pubPending} className="contents">
            <input type="hidden" name="brief" value={brief.id} />
            <Button type="submit" variant="primary" disabled={!canPublish}>
              Publish Brief
            </Button>
            {!canPublish && (
              <p className="mt-1 text-xs text-ink-muted">Fill in all required fields before publishing.</p>
            )}
            {pubState?.error && <p className="text-xs text-danger mt-1">{pubState.error}</p>}
          </fieldset>
        </form>
      )}
    </div>
  );
}
