'use client';
import { useRef, useState } from 'react';
import { Button } from '@signal/ui';
import { ChipField, fieldInput } from './chip-field';
import {
  isColumnSpec, keyFromLabel, MAX_COLUMNS, OPS_BY_TYPE, ROLE_LABELS, TYPE_LABELS,
  type BriefConfig, type ColumnSpec, type EvidenceRule, type Role, type ValueType,
} from '@/lib/briefs';

type Column = ColumnSpec | string;
type Row = { id: number; col: Column; isNew: boolean };

const VALUE_TYPES = Object.keys(TYPE_LABELS) as ValueType[];
const ROLES = Object.keys(ROLE_LABELS) as Role[];

/** The rules that still fit the edited columns: a removed column takes its rules with it. */
export function rulesThatFit(rules: EvidenceRule[], columns: Column[]): EvidenceRule[] {
  const specs = new Map(columns.filter(isColumnSpec).map((c) => [c.key, c]));
  return rules.flatMap((r) => {
    const col = specs.get(r.predicate);
    if (!col || !OPS_BY_TYPE[col.value_type]?.includes(r.op)) return [];
    if (r.op !== 'one_of') return [r];
    const kept = (Array.isArray(r.value) ? r.value : []).filter((v) => col.allowed_values?.includes(v));
    return kept.length ? [{ ...r, value: kept }] : [];
  });
}

function ColumnList({ columns }: { columns: Column[] }) {
  return (
    <ul className="m-0 flex list-none flex-col gap-3 p-0">
      {columns.map((c) =>
        isColumnSpec(c) ? (
          <li key={c.key} className="flex min-w-0 flex-col gap-1 border-b border-line pb-3 last:border-b-0 last:pb-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-ink">{c.label}</span>
              <span className="si-ftag si-fam-pool">{TYPE_LABELS[c.value_type] ?? c.value_type}</span>
              <span className="text-[13px] text-ink-muted">{ROLE_LABELS[c.role] ?? c.role}</span>
            </div>
            {c.value_type === 'enum' && c.allowed_values?.length ? (
              <span className="text-[14px] text-ink-body">{c.allowed_values.join(' · ')}</span>
            ) : null}
            {c.extraction_hint ? <span className="text-[14px] text-ink-muted">{c.extraction_hint}</span> : null}
          </li>
        ) : (
          <li key={c} className="flex flex-wrap items-center gap-2 border-b border-line pb-3 last:border-b-0 last:pb-0">
            <span className="si-mono">{c}</span>
            <span className="text-[13px] text-ink-muted">A workspace column, kept as it is</span>
          </li>
        ),
      )}
    </ul>
  );
}

/** Allowed values of an enum column, as chips. */
function EnumChips({ values, onChange }: { values: string[]; onChange: (next: string[]) => void }) {
  const [text, setText] = useState('');
  const add = () => {
    const v = text.trim();
    if (v && !values.includes(v)) onChange([...values, v]);
    setText('');
  };
  return (
    <div className="flex flex-col gap-1.5 sm:col-span-2">
      <span className="text-[13px] text-ink-muted">Allowed values (2 to 12)</span>
      {values.length ? (
        <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
          {values.map((v) => (
            <li key={v} className="si-chip max-w-full border-line-strong bg-surface-sunken text-ink">
              <span className="min-w-0 truncate">{v}</span>
              <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}
                className="cursor-pointer border-0 bg-transparent p-0 text-ink-muted">✕</button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex gap-2">
        <input aria-label="New allowed value" value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          placeholder="Type a value" className={fieldInput} />
        <Button size="sm" onClick={add}>Add</Button>
      </div>
    </div>
  );
}

function ColumnsEditor({ saved, onChange }: { saved: BriefConfig; onChange: (patch: Record<string, unknown>) => void }) {
  const [rows, setRows] = useState<Row[]>(() => (saved.predicates ?? []).map((col, id) => ({ id, col, isNew: false })));
  const nextId = useRef(rows.length);
  const savedRules = saved.evidence_rules ?? [];

  const commit = (next: Row[]) => {
    setRows(next);
    const columns = next.map((r) => r.col);
    onChange({ predicates: columns, evidence_rules: rulesThatFit(savedRules, columns) });
  };
  const update = (id: number, patch: Partial<ColumnSpec>) =>
    commit(rows.map((r) => {
      if (r.id !== id || !isColumnSpec(r.col)) return r;
      const col = { ...r.col, ...patch };
      // The key comes from the label until the column is saved; after that it never changes.
      if (r.isNew && patch.label !== undefined) col.key = keyFromLabel(patch.label);
      return { ...r, col };
    }));
  const add = () =>
    commit([...rows, {
      id: nextId.current++, isNew: true,
      col: { key: '', label: '', value_type: 'text', allowed_values: null, extraction_hint: '', decay_half_life_days: null, role: 'info' },
    }]);

  const dropped = savedRules.length - rulesThatFit(savedRules, rows.map((r) => r.col)).length;

  return (
    <div className="flex flex-col gap-4">
      {rows.map((r, i) => {
        const c = r.col;
        if (!isColumnSpec(c)) {
          return (
            <fieldset key={r.id} className="m-0 flex flex-wrap items-center justify-between gap-2 rounded-sm border border-line p-3">
              <legend className="px-1 text-[13px] text-ink-muted">Column {i + 1}</legend>
              <span className="si-mono">{c}</span>
              <Button size="sm" variant="quiet" onClick={() => commit(rows.filter((x) => x.id !== r.id))}>Remove column</Button>
            </fieldset>
          );
        }
        return (
          <fieldset key={r.id} className="m-0 grid min-w-0 gap-3 rounded-sm border border-line p-3 sm:grid-cols-2">
            <legend className="px-1 text-[13px] text-ink-muted">Column {i + 1}</legend>
            <div className="flex min-w-0 flex-col gap-1">
              <label className="flex min-w-0 flex-col gap-1">
                <span className="text-[13px] text-ink-muted">Label</span>
                <input aria-label="Label" value={c.label} maxLength={80} onChange={(e) => update(r.id, { label: e.target.value })} className={fieldInput} />
              </label>
              <span className="si-meta break-all">Key: {c.key || 'made from the label'}</span>
            </div>
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-[13px] text-ink-muted">Type</span>
              <select aria-label="Type" value={c.value_type} className={fieldInput}
                onChange={(e) => {
                  const value_type = e.target.value as ValueType;
                  update(r.id, { value_type, allowed_values: value_type === 'enum' ? (c.allowed_values ?? []) : null });
                }}>
                {VALUE_TYPES.map((t) => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}
              </select>
            </label>
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-[13px] text-ink-muted">Role</span>
              <select aria-label="Role" value={c.role} onChange={(e) => update(r.id, { role: e.target.value as Role })} className={fieldInput}>
                {ROLES.map((role) => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}
              </select>
            </label>
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-[13px] text-ink-muted">Hint: what the AI looks for</span>
              <input aria-label="Hint" value={c.extraction_hint ?? ''} maxLength={300} onChange={(e) => update(r.id, { extraction_hint: e.target.value })} className={fieldInput} />
            </label>
            {c.value_type === 'enum' ? (
              <EnumChips values={c.allowed_values ?? []} onChange={(allowed_values) => update(r.id, { allowed_values })} />
            ) : null}
            <div className="sm:col-span-2">
              <Button size="sm" variant="quiet" onClick={() => commit(rows.filter((x) => x.id !== r.id))}>Remove column</Button>
            </div>
          </fieldset>
        );
      })}
      {dropped > 0 ? (
        <p className="m-0 text-[13px] text-ink">
          Removing a column also removes its rules: {dropped} {dropped === 1 ? 'rule goes' : 'rules go'} when you save.
        </p>
      ) : null}
      <div>
        <Button size="sm" onClick={add} disabled={rows.length >= MAX_COLUMNS}>Add a column</Button>
        {rows.length >= MAX_COLUMNS ? <span className="ml-2 text-[13px] text-ink-muted">A Brief has at most {MAX_COLUMNS} columns.</span> : null}
      </div>
    </div>
  );
}

/** "Columns the AI fills": the facts extracted for every company, each with its scoring role. */
export function ColumnsField({ briefId, config, disabled }: { briefId: string; config: BriefConfig; disabled: boolean }) {
  return (
    <ChipField briefId={briefId} fieldName="predicates" fields={['predicates', 'evidence_rules']} label="Columns the AI fills"
      disabled={disabled} currentConfig={config as Record<string, unknown>}
      isEmpty={(c) => !((c.predicates as unknown[] | undefined)?.length)}
      renderValue={(c) => <ColumnList columns={(c.predicates as Column[] | undefined) ?? []} />}
      renderEditor={(_draft, onChange) => <ColumnsEditor saved={config} onChange={onChange} />}
    />
  );
}
