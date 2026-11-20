'use client';
import { useEffect, useRef, useState } from 'react';
import { Button } from '@signal/ui';
import { ChipField, fieldInput } from './chip-field';
import {
  columnSpecs, describeRule, isFormulaColumn, MAX_RULES, OP_LABELS, OPS_BY_TYPE, parseDays,
  type BriefConfig, type ColumnSpec, type EvidenceRule, type RuleOp,
} from '@/lib/briefs';

/** `ageError`: what is wrong with the typed "newer than" days; the rule keeps its last valid value. */
type Row = { id: number; rule: EvidenceRule; ageError: string | null };

/** The value a new op starts with: a list for one_of/contains, nothing otherwise. */
const startValue = (op: RuleOp): EvidenceRule['value'] => (op === 'one_of' || op === 'contains' ? [] : null);

function RuleValue({ rule, column, onChange }: { rule: EvidenceRule; column: ColumnSpec | undefined; onChange: (value: EvidenceRule['value']) => void }) {
  if (rule.op === 'one_of') {
    const chosen = Array.isArray(rule.value) ? rule.value : [];
    return (
      <fieldset className="m-0 flex min-w-0 flex-col gap-1.5 border-0 p-0 sm:col-span-2">
        <legend className="mb-1 text-[13px] text-ink-muted">Values</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {(column?.allowed_values ?? []).map((v) => (
            <label key={v} className="flex items-center gap-2 text-[14px] text-ink">
              <input type="checkbox" checked={chosen.includes(v)}
                onChange={(e) => onChange(e.target.checked ? [...chosen, v] : chosen.filter((x) => x !== v))} />
              {v}
            </label>
          ))}
        </div>
      </fieldset>
    );
  }
  if (rule.op === 'at_least') {
    return (
      <label className="flex min-w-0 flex-col gap-1">
        <span className="text-[13px] text-ink-muted">Number</span>
        <input type="number" step="any" defaultValue={typeof rule.value === 'number' ? rule.value : ''}
          onChange={(e) => onChange(e.target.value.trim() === '' ? null : Number(e.target.value))} className={fieldInput} />
      </label>
    );
  }
  if (rule.op === 'contains') {
    return (
      <label className="flex min-w-0 flex-col gap-1">
        <span className="text-[13px] text-ink-muted">Words, separated by commas</span>
        <input defaultValue={Array.isArray(rule.value) ? rule.value.join(', ') : ''}
          onChange={(e) => onChange(e.target.value.split(',').map((x) => x.trim()).filter(Boolean))} className={fieldInput} />
      </label>
    );
  }
  return null;
}

function RulesEditor({ saved, onChange, block }: {
  saved: BriefConfig;
  onChange: (patch: Record<string, unknown>) => void;
  block: (problem: string | null) => void;
}) {
  const allColumns = columnSpecs(saved);
  // A formula's value has no quote, so it can never satisfy a rule: formula columns are not offered.
  const columns = allColumns.filter((c) => !isFormulaColumn(c));
  const [rows, setRows] = useState<Row[]>(() => (saved.evidence_rules ?? []).map((rule, id) => ({ id, rule, ageError: null })));
  const nextId = useRef(rows.length);

  const commit = (next: Row[]) => {
    setRows(next);
    onChange({ evidence_rules: next.map((r) => r.rule) });
  };
  const update = (id: number, patch: Partial<EvidenceRule>) =>
    commit(rows.map((r) => (r.id === id ? { ...r, rule: { ...r.rule, ...patch } } : r)));
  const setAge = (id: number, input: HTMLInputElement) => {
    const { value, error } = parseDays(input.value, input.validity.badInput);
    commit(rows.map((r) => (r.id === id ? { ...r, ageError: error, rule: error ? r.rule : { ...r.rule, max_age_days: value } } : r)));
  };
  const firstProblem = rows.find((r) => r.ageError)?.ageError ?? null;
  useEffect(() => block(firstProblem), [block, firstProblem]);
  const ruleFor = (col: ColumnSpec, kind: EvidenceRule['kind'] = 'requires'): EvidenceRule => {
    const op = OPS_BY_TYPE[col.value_type][0];
    return { predicate: col.key, kind, op, value: startValue(op), min_sources: 1, max_age_days: null };
  };

  if (!allColumns.length) return <p className="m-0 text-[14px] text-ink-muted">Add a column first: every rule tests one column.</p>;
  if (!columns.length) {
    return <p className="m-0 text-[14px] text-ink-muted">Add a column found in sources first: a rule cannot test a formula column.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {rows.map((r, i) => {
        const { rule } = r;
        const column = columns.find((c) => c.key === rule.predicate);
        const ops = column ? OPS_BY_TYPE[column.value_type] : [rule.op];
        return (
          <fieldset key={r.id} className="m-0 grid min-w-0 gap-3 rounded-sm border border-line p-3 sm:grid-cols-2">
            <legend className="px-1 text-[13px] text-ink-muted">Rule {i + 1}</legend>
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-[13px] text-ink-muted">Column</span>
              <select aria-label="Column" value={rule.predicate} className={fieldInput}
                onChange={(e) => {
                  const col = columns.find((c) => c.key === e.target.value);
                  if (col) update(r.id, { ...ruleFor(col, rule.kind), min_sources: rule.min_sources, max_age_days: rule.max_age_days });
                }}>
                {column ? null : <option value={rule.predicate}>{rule.predicate}</option>}
                {columns.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
              </select>
            </label>
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-[13px] text-ink-muted">Effect</span>
              <select aria-label="Effect" value={rule.kind} onChange={(e) => update(r.id, { kind: e.target.value as EvidenceRule['kind'] })} className={fieldInput}>
                <option value="requires">Requires</option>
                <option value="disqualifies">Rules out</option>
              </select>
            </label>
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-[13px] text-ink-muted">Test</span>
              <select aria-label="Test" value={rule.op} className={fieldInput}
                onChange={(e) => { const op = e.target.value as RuleOp; update(r.id, { op, value: startValue(op) }); }}>
                {ops.map((op) => <option key={op} value={op}>{OP_LABELS[op] ?? op}</option>)}
              </select>
            </label>
            {/* Remounts when the test changes, so the value input starts from the new test's value. */}
            <RuleValue key={`${rule.predicate}:${rule.op}`} rule={rule} column={column} onChange={(value) => update(r.id, { value })} />
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-[13px] text-ink-muted">Sources needed</span>
              <select aria-label="Sources needed" value={rule.min_sources ?? 1} onChange={(e) => update(r.id, { min_sources: Number(e.target.value) })} className={fieldInput}>
                {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-[13px] text-ink-muted">Newer than (days; blank means any age)</span>
              <input aria-label="Newer than (days)" type="number" min={1} max={3650} step={1} defaultValue={rule.max_age_days ?? ''} placeholder="Any age"
                aria-invalid={r.ageError ? true : undefined} onChange={(e) => setAge(r.id, e.target)} className={fieldInput} />
              {r.ageError ? <span role="alert" className="text-[13px] text-danger">{r.ageError}</span> : null}
            </label>
            <p className="m-0 text-[13px] text-ink-body sm:col-span-2">{describeRule(rule, columns)}</p>
            <div className="sm:col-span-2">
              <Button size="sm" variant="quiet" onClick={() => commit(rows.filter((x) => x.id !== r.id))}>Remove rule</Button>
            </div>
          </fieldset>
        );
      })}
      <div>
        <Button size="sm" onClick={() => commit([...rows, { id: nextId.current++, rule: ruleFor(columns[0]), ageError: null }])} disabled={rows.length >= MAX_RULES}>
          Add a rule
        </Button>
        {rows.length >= MAX_RULES ? <span className="ml-2 text-[13px] text-ink-muted">A Brief has at most {MAX_RULES} rules.</span> : null}
      </div>
    </div>
  );
}

/** "How a verdict is decided": the evidence rules, each as a sentence. */
export function RulesField({ briefId, config, disabled }: { briefId: string; config: BriefConfig; disabled: boolean }) {
  return (
    <ChipField briefId={briefId} fieldName="evidence_rules" label="How a verdict is decided"
      disabled={disabled} currentConfig={config as Record<string, unknown>}
      isEmpty={(c) => !((c.evidence_rules as unknown[] | undefined)?.length)}
      renderValue={(c) => (
        <ul className="m-0 flex flex-col gap-1.5 pl-5">
          {((c.evidence_rules as EvidenceRule[] | undefined) ?? []).map((rule, i) => (
            <li key={i}>{describeRule(rule, (c.predicates as BriefConfig['predicates']) ?? [])}</li>
          ))}
        </ul>
      )}
      renderEditor={(_draft, onChange, block) => <RulesEditor saved={config} onChange={onChange} block={block} />}
    />
  );
}
