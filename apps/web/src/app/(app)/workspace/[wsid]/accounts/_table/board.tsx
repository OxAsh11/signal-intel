import Link from 'next/link';
import { Stage, Verdict } from '@signal/contracts';
import { AccountCard } from './cards';
import { groupOf, layoutOf, tableHref, type TableQuery } from './url';
import type { Column, TableRow } from './types';

const nf = new Intl.NumberFormat('en');
export const BOARD_LIMIT = 1000;

const seg = 'si-btn si-btn-sm no-underline';

/** Table | Board | By country, the Board's grouping, and Export (the current view as CSV). */
export function LayoutSwitch({ ws, q, exportHref }: { ws: string; q: TableQuery; exportHref: string }) {
  const layout = layoutOf(q);
  const group = groupOf(q);
  const item = (label: string, patch: Partial<Record<keyof TableQuery, string | null>>, on: boolean) => (
    <Link
      key={label}
      href={tableHref(ws, q, { page: 1, ...patch })}
      aria-current={on ? 'true' : undefined}
      className={`${seg} ${on ? 'font-medium' : ''}`}
      style={on ? { background: 'var(--signal-soft)' } : undefined}
    >
      {label}
    </Link>
  );
  return (
    <div className="flex flex-wrap items-center gap-2" data-layout-switch="">
      <nav aria-label="Layout" className="flex items-center gap-1">
        {item('Table', { layout: null, group: null }, layout === 'table')}
        {item('Board', { layout: 'board' }, layout === 'board')}
        {item('By country', { layout: 'country', group: null }, layout === 'country')}
      </nav>
      {layout === 'board' ? (
        <nav aria-label="Group board by" className="flex items-center gap-1">
          {item('By stage', { group: null }, group === 'stage')}
          {item('By verdict', { group: 'verdict' }, group === 'verdict')}
        </nav>
      ) : null}
      <a className={`${seg} ml-auto`} href={exportHref} download data-export="">
        Export CSV
      </a>
    </div>
  );
}

function Lane({ title, rows, ws, columns, id }: { title: string; rows: TableRow[]; ws: string; columns: Column[]; id: string }) {
  return (
    <section aria-label={title} data-lane={id} className="flex w-72 flex-none flex-col gap-2 rounded-md bg-surface-sunken p-2">
      <h2 className="m-0 flex items-center justify-between px-1 text-[13px] font-medium text-ink">
        <span className="truncate">{title}</span>
        <span className="text-ink-muted" data-count="">
          {nf.format(rows.length)}
        </span>
      </h2>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {rows.map((r) => (
          <AccountCard key={r.account_id} ws={ws} row={r} columns={columns} />
        ))}
      </ul>
      {rows.length === 0 ? <p className="m-0 px-1 py-2 text-[12px] text-ink-faint">None</p> : null}
    </section>
  );
}

const Capped = ({ shown, total }: { shown: number; total: number }) =>
  total > shown ? (
    <p className="m-0 text-[13px] text-ink-muted" data-capped="">
      Showing the first {nf.format(shown)} of {nf.format(total)} accounts. Filter to narrow this down.
    </p>
  ) : null;

/** Read-only: stage comes from activity, so a card moves when the account does (dragging waits for the CRM). */
export function Board({ ws, q, rows, columns, total }: { ws: string; q: TableQuery; rows: TableRow[]; columns: Column[]; total: number }) {
  const byVerdict = groupOf(q) === 'verdict';
  const known = Verdict.options as readonly string[];
  const lanes: { id: string; title: string; match: (r: TableRow) => boolean }[] = byVerdict
    ? [
        ...Verdict.options.map((v) => ({ id: v, title: v, match: (r: TableRow) => r.verdict === v })),
        { id: 'none', title: 'No verdict', match: (r: TableRow) => !r.verdict || !known.includes(r.verdict) },
      ]
    : Stage.options.map((s) => ({ id: s, title: s, match: (r: TableRow) => r.stage === s }));
  return (
    <div className="flex flex-col gap-3" data-board={byVerdict ? 'verdict' : 'stage'}>
      <Capped shown={rows.length} total={total} />
      <p className="m-0 text-[12px] text-ink-faint">Stage follows activity, so cards cannot be dragged yet.</p>
      <div className="flex items-start gap-3 overflow-x-auto pb-2">
        {lanes.map((l) => (
          <Lane key={l.id} id={l.id} title={l.title} ws={ws} columns={columns} rows={rows.filter(l.match)} />
        ))}
      </div>
    </div>
  );
}

/** Groups by the account's country (latest mention), biggest group first; no country goes last. */
export function ByCountry({ ws, rows, columns, total }: { ws: string; rows: TableRow[]; columns: Column[]; total: number }) {
  const groups = new Map<string, TableRow[]>();
  for (const r of rows) {
    const k = r.country?.trim() || '';
    groups.set(k, [...(groups.get(k) ?? []), r]);
  }
  const list = [...groups.entries()].sort(([a, x], [b, y]) => (a === '' ? 1 : b === '' ? -1 : y.length - x.length || a.localeCompare(b)));
  return (
    <div className="flex flex-col gap-3" data-by-country="">
      <Capped shown={rows.length} total={total} />
      <p className="m-0 text-[12px] text-ink-faint">A map arrives when sites have coordinates.</p>
      <div className="flex items-start gap-3 overflow-x-auto pb-2">
        {list.map(([country, rs]) => (
          <Lane key={country || 'none'} id={country || 'none'} title={country || 'No country'} ws={ws} columns={columns} rows={rs} />
        ))}
      </div>
    </div>
  );
}
