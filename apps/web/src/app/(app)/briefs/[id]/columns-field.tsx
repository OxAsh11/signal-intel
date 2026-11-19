'use client';
import { useEffect, useRef, useState } from 'react';
import { formulaProblem, formulaReads, renameFormulaRef } from '@signal/formula';
import { Button } from '@signal/ui';
import { ChipField, fieldInput } from './chip-field';
import {
  columnKeyProblem, columnsFromWorkspace, columnSpecs, FILL_LABELS, isColumnSpec,
  isFormulaColumn, keyFromLabel, LEGACY_SEED_NOTICE, MAX_COLUMNS, OPS_BY_TYPE, ROLE_LABELS, TYPE_LABELS,
  type BriefConfig, type ColumnSpec, type EvidenceRule, type FillMethod, type Role, type ValueType, type WorkspacePredicate,
} from '@/lib/briefs';

type Column = ColumnSpec | string;
/**
 * `keyFixed`: the key already exists as a workspace predicate, so it no longer follows the label.
 * `stale` (formula rows): row id → the old key this formula still reads that column by, because
 * the formula could not follow the column's new key.
 */
type Row = { id: number; col: Column; keyFixed: boolean; stale?: Record<number, string> };

const VALUE_TYPES = Object.keys(TYPE_LABELS) as ValueType[];
const ROLES = Object.keys(ROLE_LABELS) as Role[];
const FILLS = Object.keys(FILL_LABELS) as FillMethod[];

/** Why a rule goes when the columns are saved. */
type RuleLoss = 'removed' | 'changed' | 'formula';

/**
 * The saved rules against the edited columns. Each rule follows its column by row, not by key, so
 * a key that changed with its label takes the rule along. A rule goes only when its column was
 * removed, became a formula (a derived value has no quote, so it can never satisfy a rule), or
 * when its column's type or values no longer allow its test.
 */
function rulesForRows(links: { rule: EvidenceRule; rowId: number | null }[], rows: Row[]) {
  const kept: EvidenceRule[] = [];
  const lost: RuleLoss[] = [];
  for (const { rule, rowId } of links) {
    const col = rows.find((r) => r.id === rowId)?.col;
    if (!col || !isColumnSpec(col)) {
      lost.push('removed');
      continue;
    }
    if (isFormulaColumn(col)) {
      lost.push('formula');
      continue;
    }
    const [fit] = rulesThatFit([{ ...rule, predicate: col.key }], [col]);
    if (fit) kept.push(fit);
    else lost.push('changed');
  }
  return { kept, lost };
}

function lossNote(lost: RuleLoss[]): string {
  const n = (k: RuleLoss) => lost.filter((l) => l === k).length;
  const rules = (k: number) => `${k} ${k === 1 ? 'rule' : 'rules'}`;
  const parts = [];
  if (n('removed')) parts.push(`${rules(n('removed'))} of a removed column`);
  if (n('formula')) parts.push(`${rules(n('formula'))} of a column now filled by a formula`);
  if (n('changed')) parts.push(`${rules(n('changed'))} whose column's type or values no longer allow the test`);
  return `When you save, ${parts.join(' and ')} ${lost.length === 1 ? 'goes' : 'go'}.`;
}

/** The rules that still fit the edited columns: a removed or formula column takes its rules with it. */
export function rulesThatFit(rules: EvidenceRule[], columns: Column[]): EvidenceRule[] {
  const specs = new Map(columns.flatMap((c) => (isColumnSpec(c) && !isFormulaColumn(c) ? [[c.key, c] as const] : [])));
  return rules.flatMap((r) => {
    const col = specs.get(r.predicate);
    if (!col || !OPS_BY_TYPE[col.value_type]?.includes(r.op)) return [];
    if (r.op !== 'one_of') return [r];
    const kept = (Array.isArray(r.value) ? r.value : []).filter((v) => col.allowed_values?.includes(v));
    return kept.length ? [{ ...r, value: kept }] : [];
  });
}

/** The labels of the columns a formula reads (a key with no column shows as itself). */
function inputLabels(keys: string[], columns: Column[]): string {
  return keys.map((k) => columns.find((c): c is ColumnSpec => isColumnSpec(c) && c.key === k)?.label ?? k).join(' · ');
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
              <span className="text-[13px] text-ink-muted">{FILL_LABELS[c.fill_method ?? 'extract'] ?? c.fill_method}</span>
            </div>
            {c.value_type === 'enum' && c.allowed_values?.length ? (
              <span className="text-[14px] text-ink-body">{c.allowed_values.join(' · ')}</span>
            ) : null}
            {isFormulaColumn(c) ? (
              <>
                <code className="si-mono min-w-0 break-all rounded-sm bg-surface-sunken px-2 py-1 text-[13px] text-ink">{c.formula}</code>
                {c.inputs?.length ? <span className="text-[13px] text-ink-muted">Reads {inputLabels(c.inputs, columns)}</span> : null}
              </>
            ) : c.extraction_hint ? (
              <span className="text-[14px] text-ink-muted">{c.extraction_hint}</span>
            ) : null}
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

/** A formula column with this expression; its inputs are the columns the expression reads. */
function withFormula(col: ColumnSpec, formula: string): ColumnSpec {
  return { ...col, formula, inputs: formulaReads(formula) ?? [] };
}

function withoutKey(stale: Record<number, string> | undefined, id: number): Record<number, string> {
  return Object.fromEntries(Object.entries(stale ?? {}).filter(([k]) => Number(k) !== id));
}

/**
 * Row `movedId`'s key changed from `from` to `to`: each formula that reads it is rewritten to read
 * `to`. A formula that cannot follow safely (the new key is a word the language reserves, a key
 * it already reads, or the formula does not parse) keeps its text and remembers the key it still
 * uses, so the next key of that column can be tried again and the editor can say what happened.
 */
function repoint(rows: Row[], movedId: number, from: string, to: string): Row[] {
  return rows.map((r) => {
    if (!isFormulaColumn(r.col)) return r;
    const used = r.stale?.[movedId] ?? from;
    const stale = withoutKey(r.stale, movedId);
    const expr = r.col.formula ?? '';
    if (used === to) return { ...r, stale };
    const next = renameFormulaRef(expr, used, to);
    if (next === null) return { ...r, stale: { ...stale, [movedId]: used } };
    return next === expr ? { ...r, stale } : { ...r, stale, col: withFormula(r.col, next) };
  });
}

/** A formula that still reads a column by a key the column no longer has. */
function staleProblem(row: Row, rows: Row[]): string | null {
  const reads = isColumnSpec(row.col) ? formulaReads(row.col.formula) : null;
  if (!reads) return null;
  for (const [id, used] of Object.entries(row.stale ?? {})) {
    const target = rows.find((r) => r.id === Number(id))?.col;
    if (!target || !isColumnSpec(target) || !reads.includes(used)) continue;
    return `Formula reads "${used}", the old key of "${target.label}", and cannot follow its new key "${target.key}". Change the label or the formula.`;
  }
  return null;
}

function ColumnsEditor({ start, saved, existing, seeded, onChange, block }: {
  start: Column[];
  saved: BriefConfig;
  existing: Map<string, WorkspacePredicate>;
  seeded: boolean;
  onChange: (patch: Record<string, unknown>) => void;
  block: (problem: string | null) => void;
}) {
  const [rows, setRows] = useState<Row[]>(() =>
    start.map((col, id) => ({ id, col, keyFixed: existing.has(isColumnSpec(col) ? col.key : col) })));
  const nextId = useRef(rows.length);
  // Each saved rule is tied to the row of its column when the editor opens (rows start at id = index).
  const [ruleLinks] = useState(() => (saved.evidence_rules ?? []).map((rule) => {
    const at = start.findIndex((c) => (isColumnSpec(c) ? c.key : c) === rule.predicate);
    return { rule, rowId: at >= 0 ? at : null };
  }));

  const commit = (next: Row[]) => {
    setRows(next);
    onChange({ predicates: next.map((r) => r.col), evidence_rules: rulesForRows(ruleLinks, next).kept });
  };
  const update = (id: number, patch: Partial<ColumnSpec>) => {
    const moves: { from: string; to: string }[] = [];
    const next = rows.map((r, i) => {
      if (r.id !== id || !isColumnSpec(r.col)) return r;
      const col = { ...r.col, ...patch };
      // The key follows the label until the workspace collects it; after that it never changes.
      if (!r.keyFixed && patch.label !== undefined) col.key = keyFromLabel(patch.label, i + 1);
      if (r.col.key && col.key !== r.col.key) moves.push({ from: r.col.key, to: col.key });
      return { ...r, col };
    });
    // Formulas follow a column whose key changed, as its rules do.
    commit(moves.reduce((acc, move) => repoint(acc, id, move.from, move.to), next));
  };
  // A formula's text is its own: what it reads is always what it says.
  const setFormula = (id: number, formula: string) =>
    commit(rows.map((r) => {
      if (r.id !== id || !isColumnSpec(r.col)) return r;
      const reads = formulaReads(formula);
      const stale = Object.fromEntries(Object.entries(r.stale ?? {}).filter(([, key]) => !reads || reads.includes(key)));
      return { ...r, stale, col: withFormula(r.col, formula) };
    }));
  const setFill = (id: number, fill: FillMethod) =>
    commit(rows.map((r) => {
      if (r.id !== id || !isColumnSpec(r.col)) return r;
      if (fill === 'formula') return { ...r, stale: {}, col: withFormula({ ...r.col, fill_method: 'formula' }, r.col.formula ?? '') };
      const col: ColumnSpec = { ...r.col, fill_method: 'extract', formula: null };
      delete col.inputs;
      return { ...r, stale: {}, col };
    }));
  const add = () =>
    commit([...rows, {
      id: nextId.current++, keyFixed: false,
      col: { key: '', label: '', value_type: 'text', allowed_values: null, extraction_hint: '', decay_half_life_days: null, role: 'info' },
    }]);

  const specs = rows.flatMap((r) => (isColumnSpec(r.col) ? [r.col] : []));
  const legacyKeys = rows.flatMap((r) => (isColumnSpec(r.col) ? [] : [r.col]));
  // A new column has no key until it has a label; only a key that is there can be wrong.
  const problems = new Map(rows.flatMap((r) => {
    const c = r.col;
    if (!isColumnSpec(c) || (!c.key && !c.label)) return [];
    const others = specs.filter((o) => o !== c);
    const problem = columnKeyProblem(c, others, existing.get(c.key));
    return problem ? [[r.id, problem] as const] : [];
  }));
  const formulaProblems = new Map(rows.flatMap((r) => {
    const c = r.col;
    if (!isFormulaColumn(c)) return [];
    const problem = staleProblem(r, rows) ?? formulaProblem(c.formula, c.key, specs.map((o) => o.key), legacyKeys);
    return problem ? [[r.id, problem] as const] : [];
  }));
  const firstProblem = problems.values().next().value ?? formulaProblems.values().next().value ?? null;
  useEffect(() => block(firstProblem), [block, firstProblem]);

  const { lost } = rulesForRows(ruleLinks, rows);

  return (
    <div className="flex flex-col gap-4">
      {seeded ? <p className="m-0 text-[14px] text-ink">{LEGACY_SEED_NOTICE}</p> : null}
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
        // The workspace already collects this key as this type, filled this way: they stay.
        const known = r.keyFixed ? existing.get(c.key) : undefined;
        const typeFixed = known?.value_type === c.value_type;
        const fill = c.fill_method ?? 'extract';
        const fillFixed = !!known && (known.fill_method ?? 'extract') === fill;
        const formula = isFormulaColumn(c);
        const formulaError = formulaProblems.get(r.id);
        return (
          <fieldset key={r.id} className="m-0 grid min-w-0 gap-3 rounded-sm border border-line p-3 sm:grid-cols-2">
            <legend className="px-1 text-[13px] text-ink-muted">Column {i + 1}</legend>
            <div className="flex min-w-0 flex-col gap-1">
              <label className="flex min-w-0 flex-col gap-1">
                <span className="text-[13px] text-ink-muted">Label</span>
                <input aria-label="Label" value={c.label} maxLength={80} onChange={(e) => update(r.id, { label: e.target.value })} className={fieldInput} />
              </label>
              <span className="si-meta break-all">Key: {c.key || 'made from the label'}</span>
              {problems.get(r.id) ? <span role="alert" className="text-[13px] text-danger">{problems.get(r.id)}</span> : null}
            </div>
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-[13px] text-ink-muted">Filled by</span>
              <select aria-label="Filled by" value={fill} className={fieldInput} disabled={fillFixed}
                onChange={(e) => setFill(r.id, e.target.value as FillMethod)}>
                {FILLS.map((f) => <option key={f} value={f}>{FILL_LABELS[f]}</option>)}
              </select>
              {fillFixed ? <span className="text-[13px] text-ink-muted">The workspace already fills this column this way, so it stays.</span> : null}
            </label>
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-[13px] text-ink-muted">Type</span>
              <select aria-label="Type" value={c.value_type} className={fieldInput}
                disabled={typeFixed}
                onChange={(e) => {
                  const value_type = e.target.value as ValueType;
                  update(r.id, { value_type, allowed_values: value_type === 'enum' ? (c.allowed_values ?? []) : null });
                }}>
                {VALUE_TYPES.map((t) => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}
              </select>
              {typeFixed ? <span className="text-[13px] text-ink-muted">The workspace already collects this column, so its type stays.</span> : null}
            </label>
            <label className="flex min-w-0 flex-col gap-1">
              <span className="text-[13px] text-ink-muted">Role</span>
              <select aria-label="Role" value={c.role} onChange={(e) => update(r.id, { role: e.target.value as Role })} className={fieldInput}>
                {ROLES.map((role) => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}
              </select>
            </label>
            {formula ? null : (
              <label className="flex min-w-0 flex-col gap-1">
                <span className="text-[13px] text-ink-muted">Hint: what the AI looks for</span>
                <input aria-label="Hint" value={c.extraction_hint ?? ''} maxLength={300} onChange={(e) => update(r.id, { extraction_hint: e.target.value })} className={fieldInput} />
              </label>
            )}
            {formula ? (
              <div className="flex min-w-0 flex-col gap-1 sm:col-span-2">
                <label className="flex min-w-0 flex-col gap-1">
                  <span className="text-[13px] text-ink-muted">Formula: column keys, @name, @domain and functions such as coalesce, if, contains</span>
                  <textarea aria-label="Formula" value={c.formula ?? ''} maxLength={500} rows={2} spellCheck={false}
                    aria-invalid={formulaError ? true : undefined}
                    onChange={(e) => setFormula(r.id, e.target.value)} className={`${fieldInput} si-mono`} />
                </label>
                {formulaError ? (
                  <span role="alert" className="text-[13px] text-danger">{formulaError}</span>
                ) : c.inputs?.length ? (
                  <span className="text-[13px] text-ink-muted">Reads {inputLabels(c.inputs, rows.map((x) => x.col))}</span>
                ) : null}
              </div>
            ) : null}
            {c.value_type === 'enum' ? (
              <EnumChips values={c.allowed_values ?? []} onChange={(allowed_values) => update(r.id, { allowed_values })} />
            ) : null}
            <div className="sm:col-span-2">
              <Button size="sm" variant="quiet" onClick={() => commit(rows.filter((x) => x.id !== r.id))}>Remove column</Button>
            </div>
          </fieldset>
        );
      })}
      {lost.length ? <p className="m-0 text-[13px] text-ink">{lossNote(lost)}</p> : null}
      <div>
        <Button size="sm" onClick={add} disabled={rows.length >= MAX_COLUMNS}>Add a column</Button>
        {rows.length >= MAX_COLUMNS ? <span className="ml-2 text-[13px] text-ink-muted">A Brief has at most {MAX_COLUMNS} columns.</span> : null}
      </div>
    </div>
  );
}

/** "Columns the AI fills": the facts extracted for every company, each with its scoring role. */
export function ColumnsField({ briefId, config, disabled, workspacePredicates }: {
  briefId: string;
  config: BriefConfig;
  disabled: boolean;
  workspacePredicates: WorkspacePredicate[];
}) {
  const existing = new Map(workspacePredicates.map((p) => [p.key, p]));
  // A Brief without column specs collects every workspace column: its editor starts from them.
  const seed = columnSpecs(config).length ? [] : columnsFromWorkspace(workspacePredicates);
  return (
    <ChipField briefId={briefId} fieldName="predicates" fields={['predicates', 'evidence_rules']} label="Columns the AI fills"
      disabled={disabled} currentConfig={config as Record<string, unknown>}
      isEmpty={(c) => !((c.predicates as unknown[] | undefined)?.length)}
      renderValue={(c) => <ColumnList columns={(c.predicates as Column[] | undefined) ?? []} />}
      editorStart={seed.length ? (c) => ({ ...c, predicates: seed, evidence_rules: [] }) : undefined}
      renderEditor={(draft, onChange, block) => (
        <ColumnsEditor start={(draft.predicates as Column[] | undefined) ?? []} saved={config} existing={existing}
          seeded={seed.length > 0} onChange={onChange} block={block} />
      )}
    />
  );
}
