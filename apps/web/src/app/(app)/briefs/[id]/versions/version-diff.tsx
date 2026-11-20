'use client';
import { useRouter } from 'next/navigation';
import {
  describeRule, FILL_LABELS, isColumnSpec, isFormulaColumn, ROLE_LABELS, TYPE_LABELS,
  type BriefConfig, type ColumnSpec, type EvidenceRule,
} from '@/lib/briefs';

type Version = { id: string; version: number; config: Record<string, unknown>; published_at: string; change_note: string | null };

const FIELDS: { key: string; label: string }[] = [
  { key: 'offer', label: 'Offer' },
  { key: 'hypothesis', label: 'Hypothesis' },
  { key: 'examples', label: 'Examples' },
  { key: 'sources', label: 'Sources' },
  { key: 'icp', label: 'ICP' },
  { key: 'personas', label: 'Personas' },
  { key: 'predicates', label: 'Columns' },
  { key: 'evidence_rules', label: 'Verdict rules' },
  { key: 'assumptions', label: 'Assumptions' },
  { key: 'seeds', label: 'Seeds' },
  { key: 'research_template', label: 'Research template' },
  { key: 'scoring', label: 'Scoring' },
  { key: 'budget', label: 'Budget' },
];

/**
 * One column as people would say it: label, type, role and how it is filled; a formula shows its
 * expression, so a formula edit is visible in the comparison.
 */
function describeColumn(c: ColumnSpec): string {
  const parts = [c.label, TYPE_LABELS[c.value_type] ?? c.value_type, ROLE_LABELS[c.role] ?? c.role];
  const fill = c.fill_method ?? 'extract';
  const how = FILL_LABELS[fill] ?? fill;
  parts.push(isFormulaColumn(c) ? `${how}: ${c.formula ?? ''}` : how);
  return parts.join(' · ');
}

function display(value: unknown, key: string, config: Record<string, unknown>): string {
  if (value === null || value === undefined) return '—';
  // Columns, rules and assumptions read as people would say them, not as JSON.
  if (key === 'predicates' && Array.isArray(value)) {
    if (!value.length) return '—';
    return (value as NonNullable<BriefConfig['predicates']>)
      .map((c) => (isColumnSpec(c) ? describeColumn(c) : c))
      .join('\n');
  }
  if (key === 'evidence_rules' && Array.isArray(value)) {
    if (!value.length) return '—';
    const columns = Array.isArray(config.predicates) ? (config.predicates as NonNullable<BriefConfig['predicates']>) : [];
    return (value as EvidenceRule[]).map((r) => describeRule(r, columns)).join('\n');
  }
  if (key === 'assumptions' && Array.isArray(value)) return value.length ? value.join('\n') : '—';
  if (typeof value === 'string') return value || '—';
  return JSON.stringify(value, null, 2);
}

export function VersionDiff({ versions, vA, vB, briefId }:
  { versions: Version[]; vA: Version | null; vB: Version | null; briefId: string }) {
  const router = useRouter();
  const setVersions = (a: number, b: number) =>
    router.push(`/briefs/${briefId}/versions?a=${a}&b=${b}`);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-3 items-center">
        <select value={vA?.version ?? ''} onChange={e => setVersions(Number(e.target.value), vB?.version ?? 1)}
          className="rounded border border-line bg-surface px-3 py-2 text-sm">
          {versions.map(v => <option key={v.id} value={v.version}>v{v.version} — {new Date(v.published_at).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' })}</option>)}
        </select>
        <span className="text-ink-muted">vs</span>
        <select value={vB?.version ?? ''} onChange={e => setVersions(vA?.version ?? 1, Number(e.target.value))}
          className="rounded border border-line bg-surface px-3 py-2 text-sm">
          {versions.map(v => <option key={v.id} value={v.version}>v{v.version} — {new Date(v.published_at).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' })}</option>)}
        </select>
      </div>

      {vA && vB && (
        <table className="si-table">
          <thead>
            <tr>
              <th className="text-left pb-2 w-32 text-ink-muted font-medium">Field</th>
              <th className="text-left pb-2 pl-4 text-ink-muted font-medium">v{vA.version}</th>
              <th className="text-left pb-2 pl-4 text-ink-muted font-medium">v{vB.version}</th>
            </tr>
          </thead>
          <tbody>
            {FIELDS.map(({ key, label }) => {
              const aVal = vA.config[key];
              const bVal = vB.config[key];
              const changed = JSON.stringify(aVal) !== JSON.stringify(bVal);
              return (
                <tr key={key} className={changed ? 'bg-caution/5' : ''}>
                  <td className="py-2 font-medium align-top">{label}</td>
                  <td className={`py-2 pl-4 font-mono text-xs align-top whitespace-pre-wrap ${changed ? 'text-caution' : ''}`}>
                    {display(aVal, key, vA.config)}
                  </td>
                  <td className={`py-2 pl-4 font-mono text-xs align-top whitespace-pre-wrap ${changed ? 'bg-marker-soft text-ink' : ''}`}>
                    {display(bVal, key, vB.config)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
