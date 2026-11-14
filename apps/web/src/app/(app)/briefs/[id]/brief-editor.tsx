'use client';
import { useActionState } from 'react';
import { ChipField, fieldInput } from './chip-field';
import { startPilot } from '@/app/actions/briefs';
import { Button, Card, FamilyTag } from '@signal/ui';
import type { BriefConfig } from '@/lib/briefs';

type Brief = { id: string; state: string; draft_config: BriefConfig | null };

export function isPublishable(cfg: BriefConfig | null): boolean {
  if (!cfg) return false;
  return !!(cfg.offer?.trim() && cfg.hypothesis?.trim() &&
    (cfg.examples?.positive?.length ?? 0) >= 3 &&
    (cfg.examples?.negative?.length ?? 0) >= 2 &&
    (cfg.sources?.radar_types?.length ?? 0) >= 1);
}

/** Source types in people's words. */
const SOURCE_LABELS: Record<string, string> = {
  news_publications: 'News and trade press',
  job_postings: 'Job postings',
  public_registries: 'Public registries and permits',
  tenders_procurement: 'Tenders and procurement',
  events: 'Events (speakers and exhibitors)',
  directories: 'Directories and partner lists',
  public_social: 'Public social posts',
  company_websites: 'Company websites',
};
const RADAR_TYPES = Object.keys(SOURCE_LABELS);

const lines = (arr: string[] | undefined) => (arr ?? []).join('\n');
const fromLines = (s: string) => s.split('\n').map((x) => x.trim()).filter(Boolean);
const list = (arr: string[] | undefined) => (arr?.length ? arr.join(', ') : null);

function Pairs({ rows }: { rows: [string, string | null][] }) {
  const shown = rows.filter(([, v]) => v);
  if (!shown.length) return <span className="text-ink-muted">Not drafted yet.</span>;
  return (
    <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-3">
      {shown.map(([k, v]) => (
        <div key={k} className="flex flex-col gap-0.5">
          <dt className="text-[13px] text-ink-muted">{k}</dt>
          <dd className="m-0 font-medium text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * Brief Studio, step 2: the AI's draft as plain statements. Each part shows what the AI wrote;
 * "Change" opens an editor for that part only.
 */
export function BriefEditor({ brief, canEdit }: { brief: Brief; canEdit: boolean; workspaceId: string }) {
  const cfg: BriefConfig = brief.draft_config ?? {};
  const disabled = !canEdit;
  const c0 = cfg as Record<string, unknown>;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <ChipField briefId={brief.id} fieldName="hypothesis" label="The signal we look for" required disabled={disabled} currentConfig={c0}
        renderValue={(c) => <p className="m-0 max-w-[70ch] text-[17px] leading-[1.55]">{String(c.hypothesis ?? '')}</p>}
        renderEditor={(c, onChange) => (
          <label className="flex flex-col gap-1">
            <span className="text-[13px] text-ink-muted">The public event that marks a company as ready to buy.</span>
            <textarea rows={3} defaultValue={String(c.hypothesis ?? '')} onChange={(e) => onChange({ ...c, hypothesis: e.target.value })} className={`${fieldInput} resize-y`} />
          </label>
        )} />

      <ChipField briefId={brief.id} fieldName="icp" label="Who fits" disabled={disabled} currentConfig={c0}
        isEmpty={(c) => !Object.values((c.icp as BriefConfig['icp']) ?? {}).some((v) => (v as string[] | undefined)?.length)}
        renderValue={(c) => {
          const icp = (c.icp as BriefConfig['icp']) ?? {};
          return <Pairs rows={[['Countries', list(icp.geographies)], ['Industries', list(icp.verticals)], ['Size', list(icp.size_bands)], ['Their role', list(icp.commercial_roles)]]} />;
        }}
        renderEditor={(c, onChange) => {
          const icp = (c.icp as BriefConfig['icp'] | undefined) ?? {};
          const fields = [['geographies', 'Countries'], ['verticals', 'Industries'], ['size_bands', 'Size'], ['commercial_roles', 'Their role']] as const;
          return (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
              {fields.map(([field, label]) => (
                <label key={field} className="flex flex-col gap-1">
                  <span className="text-[13px] text-ink-muted">{label} (one per line)</span>
                  <textarea rows={3} defaultValue={lines(icp[field])} onChange={(e) => onChange({ ...c, icp: { ...icp, [field]: fromLines(e.target.value) } })} className={`${fieldInput} resize-y`} />
                </label>
              ))}
            </div>
          );
        }} />

      <ChipField briefId={brief.id} fieldName="examples" label="Examples the AI learns from" required disabled={disabled} currentConfig={c0}
        renderValue={(c) => {
          const ex = (c.examples as { positive?: string[]; negative?: string[] } | undefined) ?? {};
          return (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-4">
              <div className="flex flex-col gap-1.5">
                <span className="font-medium text-verdict-confirmed">● Match</span>
                {(ex.positive ?? []).map((x) => <span key={x}>{x}</span>)}
              </div>
              <div className="flex flex-col gap-1.5">
                <span className="font-medium text-danger">✕ Not a match</span>
                {(ex.negative ?? []).map((x) => <span key={x}>{x}</span>)}
              </div>
            </div>
          );
        }}
        renderEditor={(c, onChange) => {
          const ex = (c.examples as { positive?: string[]; negative?: string[] } | undefined) ?? {};
          return (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-3">
              <label className="flex flex-col gap-1">
                <span className="text-[13px] text-ink-muted">Match: at least 3, one per line</span>
                <textarea rows={5} defaultValue={lines(ex.positive)} onChange={(e) => onChange({ ...c, examples: { ...ex, positive: fromLines(e.target.value) } })} className={`${fieldInput} resize-y`} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-[13px] text-ink-muted">Not a match: at least 2, one per line</span>
                <textarea rows={5} defaultValue={lines(ex.negative)} onChange={(e) => onChange({ ...c, examples: { ...ex, negative: fromLines(e.target.value) } })} className={`${fieldInput} resize-y`} />
              </label>
            </div>
          );
        }} />

      <ChipField briefId={brief.id} fieldName="sources" label="Where we will look" required disabled={disabled} currentConfig={c0}
        isEmpty={(c) => ((c.sources as { radar_types?: string[] } | undefined)?.radar_types?.length ?? 0) === 0}
        renderValue={(c) => (
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
            {((c.sources as { radar_types?: string[] } | undefined)?.radar_types ?? []).map((t) => (
              <li key={t} className="si-ftag si-fam-pool">{SOURCE_LABELS[t] ?? t.replace(/_/g, ' ')}</li>
            ))}
          </ul>
        )}
        renderEditor={(c, onChange) => {
          const current = ((c.sources as { radar_types?: string[] } | undefined)?.radar_types ?? []) as string[];
          return (
            <fieldset className="m-0 grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-2 border-0 p-0">
              <legend className="mb-2 text-[13px] text-ink-muted">Choose at least one</legend>
              {RADAR_TYPES.map((t) => (
                <label key={t} className="flex items-center gap-2 text-[14px] text-ink">
                  <input type="checkbox" checked={current.includes(t)} onChange={(e) => {
                    const next = e.target.checked ? [...current, t] : current.filter((x) => x !== t);
                    onChange({ ...c, sources: { ...((c.sources as object) ?? {}), radar_types: next } });
                  }} />
                  {SOURCE_LABELS[t]}
                </label>
              ))}
            </fieldset>
          );
        }} />

      <ChipField briefId={brief.id} fieldName="personas" label="Who to contact" disabled={disabled} currentConfig={c0}
        isEmpty={(c) => !Object.values((c.personas as BriefConfig['personas']) ?? {}).some((v) => (v as string[] | undefined)?.length)}
        renderValue={(c) => {
          const p = (c.personas as BriefConfig['personas']) ?? {};
          return <Pairs rows={[['Decides', list(p.buyer)], ['Uses it', list(p.user)], ['Influences', list(p.influencer)]]} />;
        }}
        renderEditor={(c, onChange) => {
          const p = (c.personas as BriefConfig['personas'] | undefined) ?? {};
          const fields = [['buyer', 'Decides'], ['user', 'Uses it'], ['influencer', 'Influences']] as const;
          return (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
              {fields.map(([field, label]) => (
                <label key={field} className="flex flex-col gap-1">
                  <span className="text-[13px] text-ink-muted">{label}: job titles, one per line</span>
                  <textarea rows={3} defaultValue={lines(p[field])} onChange={(e) => onChange({ ...c, personas: { ...p, [field]: fromLines(e.target.value) } })} className={`${fieldInput} resize-y`} />
                </label>
              ))}
            </div>
          );
        }} />

      <ChipField briefId={brief.id} fieldName="offer" label="What you sell" required disabled={disabled} currentConfig={c0}
        renderValue={(c) => <p className="m-0">{String(c.offer ?? '')}</p>}
        renderEditor={(c, onChange) => (
          <label className="flex flex-col gap-1">
            <span className="text-[13px] text-ink-muted">In one line, what you offer these companies.</span>
            <input defaultValue={String(c.offer ?? '')} onChange={(e) => onChange({ ...c, offer: e.target.value })} className={fieldInput} />
          </label>
        )} />
    </div>
  );
}

/** Right rail on a Draft: start the pilot once the required parts are in. */
export function StartPilotCard({ brief, canEdit }: { brief: Brief; canEdit: boolean }) {
  const [state, formAction, pending] = useActionState(startPilot, {});
  const ready = isPublishable(brief.draft_config);
  return (
    <Card family="mint" kind="feature" aria-labelledby="pilot-h">
      <FamilyTag family="mint">Next · pilot</FamilyTag>
      <h2 id="pilot-h" className="si-h2 text-[22px]">Test the Brief on 20 companies</h2>
      <ol className="m-0 flex flex-col gap-1.5 pl-5 text-[14px]">
        <li>Radars collect from the sources you chose.</li>
        <li>The AI judges 20 companies and shows the quotes behind each verdict.</li>
        <li>You mark each verdict right or wrong; wrong ones become examples.</li>
        <li>At 80% right, go live and the engine runs on every company it finds.</li>
      </ol>
      {canEdit ? (
        <form action={formAction} className="flex flex-col gap-2">
          <input type="hidden" name="brief" value={brief.id} />
          <div>
            <Button type="submit" variant="primary" disabled={!ready || pending}>Start pilot</Button>
          </div>
          <span className="text-[13px] text-ink">
            {ready ? 'Nothing is emailed during a pilot.' : 'Check the parts marked "Needed to start the pilot" first.'}
          </span>
          {state?.error ? <p className="m-0 text-[13px] text-danger">{state.error}</p> : null}
        </form>
      ) : null}
    </Card>
  );
}
