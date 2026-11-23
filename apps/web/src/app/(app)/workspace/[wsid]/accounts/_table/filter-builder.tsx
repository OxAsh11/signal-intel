'use client';

import { useRouter } from 'next/navigation';
import { useId, useState } from 'react';
import { Band, Stage, Verdict } from '@signal/contracts';
import { encodeFilter, tableHref, type TableQuery } from './url';

/** A column the filter can name (by label). */
export type FilterColumn = { key: string; label: string; value_type: string; allowed_values: string[] };

const CELL_STATES = ['empty', 'queued', 'running', 'value', 'derived', 'no_data', 'skipped', 'error', 'stale'];
const STATE_LABELS: Record<string, string> = {
  empty: 'Empty', queued: 'Queued', running: 'Running', value: 'Value', derived: 'Derived', no_data: 'No data', skipped: 'Skipped', error: 'Error', stale: 'Stale',
};
const OP_LABELS: Record<string, string> = {
  one_of: 'is', eq: 'is', neq: 'is not', gt: 'is greater than', gte: 'is at least', lt: 'is less than', lte: 'is at most',
  contains: 'contains', is_empty: 'is empty', is_not_empty: 'is not empty', is_true: 'is Yes', is_false: 'is No',
  within_days: 'is within the last … days', older_than_days: 'is older than … days',
};
const NO_VALUE = ['is_empty', 'is_not_empty', 'is_true', 'is_false'];
const MAX_CONDITIONS = 50;

type Editor = 'multi' | 'one' | 'text' | 'number' | 'date' | 'days' | 'none';
type FieldInfo = { id: string; label: string; ops: string[]; options: string[] };

/** Every field the chip builder offers: system fields, then each column by label, then run states. */
export function fieldInfos(columns: FilterColumn[]): FieldInfo[] {
  const sys: FieldInfo[] = [
    { id: 'verdict', label: 'Verdict', ops: ['one_of'], options: [...Verdict.options] },
    { id: 'band', label: 'Band', ops: ['one_of'], options: [...Band.options] },
    { id: 'stage', label: 'Stage', ops: ['one_of'], options: [...Stage.options] },
    { id: 'label', label: 'Label', ops: ['one_of'], options: [] },
    { id: 'tag', label: 'Tag', ops: ['one_of'], options: [] },
    { id: 'name', label: 'Name', ops: ['contains', 'eq', 'is_empty', 'is_not_empty'], options: [] },
    { id: 'domain', label: 'Domain', ops: ['contains', 'eq', 'is_empty', 'is_not_empty'], options: [] },
    { id: 'created_at', label: 'Created', ops: ['within_days', 'older_than_days'], options: [] },
  ];
  const cols = columns.map<FieldInfo>((c) => {
    const e = ['is_empty', 'is_not_empty'];
    const ops =
      c.value_type === 'boolean' ? ['is_true', 'is_false', ...e]
      : c.value_type === 'number' || c.value_type === 'date' ? ['eq', 'neq', 'gt', 'gte', 'lt', 'lte', ...e]
      : c.value_type === 'enum' ? ['eq', 'neq', 'one_of', ...e]
      : c.value_type === 'text' ? ['contains', 'eq', 'neq', ...e]
      : e;
    return { id: `col:${c.key}`, label: c.label, ops, options: c.allowed_values };
  });
  const runs: FieldInfo[] = [
    { id: 'run:*', label: 'Run state of any column', ops: ['one_of'], options: CELL_STATES },
    ...columns.map<FieldInfo>((c) => ({ id: `run:${c.key}`, label: `Run state of ${c.label}`, ops: ['one_of'], options: CELL_STATES })),
  ];
  return [...sys, ...cols, ...runs];
}

type Cond = { field: string; op: string; value: unknown; kind: string };
type Item = { type: 'cond'; cond: Cond } | { type: 'any'; conds: Cond[] };

const typeOf = (columns: FilterColumn[], field: string) => columns.find((c) => `col:${c.key}` === field)?.value_type ?? '';

function editorFor(columns: FilterColumn[], c: Cond): Editor {
  if (NO_VALUE.includes(c.op)) return 'none';
  if (c.op === 'within_days' || c.op === 'older_than_days') return 'days';
  if (c.op === 'one_of') return c.field === 'label' ? 'text' : 'multi';
  const t = typeOf(columns, c.field);
  if (t === 'enum') return 'one';
  if (t === 'number') return 'number';
  if (t === 'date') return 'date';
  return 'text';
}

function blank(info: FieldInfo): Cond {
  return { field: info.id, op: info.ops[0], value: info.ops[0] === 'one_of' && info.id !== 'label' ? [] : '', kind: '' };
}

function complete(columns: FilterColumn[], c: Cond): boolean {
  const ed = editorFor(columns, c);
  if (c.field === 'label' && !c.kind.trim()) return false;
  if (c.field === 'tag') return Array.isArray(c.value) && c.value.length > 0;
  if (ed === 'none') return true;
  if (ed === 'multi') return Array.isArray(c.value) && c.value.length > 0;
  const s = String(c.value ?? '').trim();
  if (ed === 'number' || ed === 'days') return s !== '' && Number.isFinite(Number(s));
  return s !== '';
}

function condAst(columns: FilterColumn[], c: Cond): Record<string, unknown> {
  const id = c.field;
  const field: Record<string, unknown> =
    id.startsWith('col:') ? { column: id.slice(4) }
    : id.startsWith('run:') ? { run: id.slice(4) }
    : id === 'label' ? { system: 'label', kind: c.kind.trim() }
    : { system: id };
  const ed = editorFor(columns, c);
  if (ed === 'none') return { field, op: c.op };
  let value: unknown = c.value;
  if (ed === 'number' || ed === 'days') value = Number(c.value);
  else if (ed === 'text' && c.op === 'one_of') value = String(c.value).split(',').map((v) => v.trim()).filter(Boolean);
  else if (ed === 'text' || ed === 'date' || ed === 'one') value = String(c.value).trim();
  if (id === 'tag') return { field, op: c.op, value: Array.isArray(c.value) ? c.value : [] };
  return { field, op: c.op, value };
}

/** The AST of the chips: every chip must hold (`all`), an any-of group holds when one of its chips does. */
export function itemsAst(columns: FilterColumn[], items: Item[]): Record<string, unknown> {
  if (items.length === 0) return {};
  return {
    all: items.map((i) => (i.type === 'cond' ? condAst(columns, i.cond) : { any: i.conds.map((c) => condAst(columns, c)) })),
  };
}

function parseCond(columns: FilterColumn[], node: unknown): Cond | null {
  if (node === null || typeof node !== 'object') return null;
  const n = node as { field?: Record<string, unknown>; op?: unknown; value?: unknown };
  if (!n.field || typeof n.op !== 'string') return null;
  let id: string;
  let kind = '';
  if (typeof n.field.column === 'string') id = `col:${n.field.column}`;
  else if (typeof n.field.run === 'string') id = `run:${n.field.run}`;
  else if (typeof n.field.system === 'string') {
    id = n.field.system;
    if (id === 'label') kind = typeof n.field.kind === 'string' ? n.field.kind : '';
  } else return null;
  const info = fieldInfos(columns).find((f) => f.id === id);
  if (!info || !info.ops.includes(n.op)) return null;
  let value: unknown = n.value ?? '';
  if (n.op === 'one_of' && id === 'label' && Array.isArray(value)) value = value.join(', ');
  else if (n.op !== 'one_of' && value !== '') value = String(value);
  return { field: id, op: n.op, value, kind };
}

/** The chips a filter AST shows as, or null when it is more than the builder can express. */
export function parseItems(columns: FilterColumn[], ast: Record<string, unknown> | null): Item[] | null {
  if (!ast || Object.keys(ast).length === 0) return [];
  const one = (node: unknown): Item | null => {
    const c = parseCond(columns, node);
    if (c) return { type: 'cond', cond: c };
    const any = (node as { any?: unknown } | null)?.any;
    if (Array.isArray(any) && Object.keys(node as object).length === 1) {
      const conds = any.map((x) => parseCond(columns, x));
      return conds.every((x): x is Cond => x !== null) ? { type: 'any', conds } : null;
    }
    return null;
  };
  const all = (ast as { all?: unknown }).all;
  if (Array.isArray(all) && Object.keys(ast).length === 1) {
    const items = all.map(one);
    return items.every((x): x is Item => x !== null) ? items : null;
  }
  const single = one(ast);
  return single ? [single] : null;
}

const control = 'rounded-sm border border-line-strong bg-surface px-2 py-1.5 text-[13px] text-ink';

function CondRow({ columns, infos, cond, onChange, onRemove }: { columns: FilterColumn[]; infos: FieldInfo[]; cond: Cond; onChange: (c: Cond) => void; onRemove: () => void }) {
  const info = infos.find((f) => f.id === cond.field) ?? infos[0];
  const ed = editorFor(columns, cond);
  const set = (patch: Partial<Cond>) => onChange({ ...cond, ...patch });
  return (
    <div role="group" aria-label="Filter" className="flex flex-wrap items-center gap-2" data-filter-chip="">
      <select aria-label="Filter field" className={control} value={cond.field} onChange={(e) => onChange(blank(infos.find((f) => f.id === e.target.value) ?? infos[0]))}>
        <optgroup label="Account">
          {infos.filter((f) => !f.id.includes(':')).map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
        </optgroup>
        {infos.some((f) => f.id.startsWith('col:')) ? (
          <optgroup label="Columns">
            {infos.filter((f) => f.id.startsWith('col:')).map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
          </optgroup>
        ) : null}
        <optgroup label="Run state">
          {infos.filter((f) => f.id.startsWith('run:')).map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
        </optgroup>
      </select>
      <select
        aria-label="Filter operator"
        className={control}
        value={cond.op}
        onChange={(e) => {
          const op = e.target.value;
          const multi = op === 'one_of' && cond.field !== 'label';
          set({ op, value: multi ? [] : Array.isArray(cond.value) ? '' : cond.value });
        }}
      >
        {info.ops.map((op) => <option key={op} value={op}>{OP_LABELS[op] ?? op}</option>)}
      </select>
      {cond.field === 'label' ? (
        <input aria-label="Label kind" className={`${control} w-32`} placeholder="Kind" value={cond.kind} maxLength={60} onChange={(e) => set({ kind: e.target.value })} />
      ) : null}
      {ed === 'multi' ? (
        <fieldset className="m-0 flex flex-wrap items-center gap-x-3 gap-y-1 border-0 p-0" aria-label="Filter value">
          {info.options.map((o) => {
            const on = Array.isArray(cond.value) && cond.value.includes(o);
            return (
              <label key={o} className="flex items-center gap-1.5 text-[13px] text-ink">
                <input
                  type="checkbox"
                  className="accent-[var(--ink)]"
                  checked={on}
                  onChange={() => set({ value: on ? (cond.value as string[]).filter((v) => v !== o) : [...((cond.value as string[]) ?? []), o] })}
                />
                {STATE_LABELS[o] ?? o}
              </label>
            );
          })}
        </fieldset>
      ) : ed === 'one' ? (
        <select aria-label="Filter value" className={control} value={String(cond.value ?? '')} onChange={(e) => set({ value: e.target.value })}>
          <option value="">Choose…</option>
          {info.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : ed === 'none' ? null : (
        <input
          aria-label="Filter value"
          className={`${control} w-44`}
          type={ed === 'number' || ed === 'days' ? 'number' : ed === 'date' ? 'date' : 'text'}
          min={ed === 'days' ? 0 : undefined}
          max={ed === 'days' ? 36500 : undefined}
          placeholder={cond.field === 'label' ? 'Comma-separated' : undefined}
          value={String(cond.value ?? '')}
          maxLength={200}
          onChange={(e) => set({ value: e.target.value })}
        />
      )}
      <button type="button" className="si-btn si-btn-quiet si-btn-sm" aria-label="Remove filter" onClick={onRemove}>
        Remove
      </button>
    </div>
  );
}

/**
 * The chip builder (T5) over the P3 filter AST. Chips combine with "all"; an "Any of" group is
 * allowed one level deep. The field picker offers Verdict, Band, Stage, Label, Name, Domain,
 * Created, every column by label and "Run state of ..."; each field offers only the ops the database
 * accepts for it. Apply writes the AST to the URL's `f` (the server still compiles it).
 */
export function FilterBuilder({ ws, q, columns, filter }: { ws: string; q: TableQuery; columns: FilterColumn[]; filter: Record<string, unknown> | null }) {
  const router = useRouter();
  const id = useId();
  const infos = fieldInfos(columns);
  const initial = parseItems(columns, filter);
  const [items, setItems] = useState<Item[]>(initial ?? []);
  const custom = initial === null;
  const conditions = items.reduce((n, i) => n + (i.type === 'cond' ? 1 : i.conds.length), 0);
  const ready = items.every((i) => (i.type === 'cond' ? complete(columns, i.cond) : i.conds.length > 0 && i.conds.every((c) => complete(columns, c))));

  const apply = (next: Item[]) => {
    const ast = itemsAst(columns, next);
    const empty = Object.keys(ast).length === 0;
    // With a view applied, an empty `f` is how "no filter" replaces the view's filter.
    router.push(tableHref(ws, q, { f: empty && !q.view ? null : encodeFilter(ast), page: 1 }));
  };
  const update = (i: number, item: Item) => setItems((prev) => prev.map((x, j) => (j === i ? item : x)));

  return (
    <div className="flex flex-col gap-3 rounded-md border border-line bg-surface p-3" data-filter-builder="" aria-labelledby={`${id}-title`}>
      <p id={`${id}-title`} className="si-label m-0">Show accounts where every filter holds</p>
      {custom ? (
        <p className="m-0 text-[13px] text-ink-muted" data-filter-custom="">
          This filter has more in it than the builder can show. Adding a filter here replaces it.
        </p>
      ) : null}
      {items.map((item, i) =>
        item.type === 'cond' ? (
          <CondRow key={i} columns={columns} infos={infos} cond={item.cond} onChange={(c) => update(i, { type: 'cond', cond: c })} onRemove={() => setItems((p) => p.filter((_, j) => j !== i))} />
        ) : (
          <div key={i} role="group" aria-label="Any of" className="flex flex-col gap-2 rounded-sm border border-line bg-surface-sunken p-2" data-filter-group="">
            <span className="si-label">Any of</span>
            {item.conds.map((c, k) => (
              <CondRow
                key={k}
                columns={columns}
                infos={infos}
                cond={c}
                onChange={(next) => update(i, { type: 'any', conds: item.conds.map((x, j) => (j === k ? next : x)) })}
                onRemove={() => setItems((p) => p.flatMap((x, j) => (j !== i ? [x] : item.conds.length > 1 ? [{ type: 'any' as const, conds: item.conds.filter((_, m) => m !== k) }] : [])))}
              />
            ))}
            <button
              type="button"
              className="si-btn si-btn-sm self-start"
              disabled={conditions >= MAX_CONDITIONS}
              onClick={() => update(i, { type: 'any', conds: [...item.conds, blank(infos[0])] })}
            >
              Add to group
            </button>
          </div>
        ),
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="si-btn si-btn-sm" disabled={conditions >= MAX_CONDITIONS} onClick={() => setItems((p) => [...p, { type: 'cond', cond: blank(infos[0]) }])}>
          Add filter
        </button>
        <button type="button" className="si-btn si-btn-sm" disabled={conditions >= MAX_CONDITIONS} onClick={() => setItems((p) => [...p, { type: 'any', conds: [blank(infos[0])] }])}>
          Add &ldquo;any of&rdquo; group
        </button>
        <span className="flex-1" />
        <button type="button" className="si-btn si-btn-quiet si-btn-sm" onClick={() => { setItems([]); router.push(tableHref(ws, q, { f: null, view: null, page: 1 })); }}>
          Clear filter
        </button>
        <button type="button" className="si-btn si-btn-primary si-btn-sm" disabled={!ready} onClick={() => apply(items)}>
          Apply filter
        </button>
      </div>
      {!ready ? <p className="m-0 text-[12px] text-ink-muted">Fill in each filter&apos;s value to apply it.</p> : null}
    </div>
  );
}
