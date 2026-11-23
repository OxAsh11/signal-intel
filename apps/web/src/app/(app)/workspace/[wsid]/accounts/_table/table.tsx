'use client';

import Link from 'next/link';
import { useCallback, useRef, useState, type KeyboardEvent } from 'react';
import { BandChip, StageBadge, VerdictBadge } from '@signal/ui';
import { Band, Stage, Verdict } from '@signal/contracts';
import { BulkBar } from './bulk-bar';
import { Cell, formatDate, WaterfallSubcolumns, type WaterfallAttempt } from './cell';
import { ColumnMenu } from './column-menu';
import { RunConfirm, type RunJob } from './run-confirm';
import type { Column, TableRow } from './types';
import type { SortKey } from './url';

/** The cell the panel opens for. Task 4's panel listens for this on `window`. */
export const OPEN_CELL_EVENT = 'si:open-cell';
export type OpenCellDetail = { accountId: string; entityId: string; predicateId: string };

const SYSTEM = [
  { id: 'name', label: 'Account', sort: 'name' },
  { id: 'verdict', label: 'Verdict', sort: 'verdict' },
  { id: 'score', label: 'Score', sort: 'priority' },
  { id: 'stage', label: 'Stage', sort: null },
  { id: 'owner', label: 'Owner', sort: null },
  { id: 'last_signal', label: 'Last signal', sort: null },
] as const;
const SYSTEM_COUNT = SYSTEM.length;
/** 768-1023 px shows this many predicate columns; the rest open in the drawer. */
const MID_COLUMNS = 2;

const sticky = 'sticky left-0 z-[1] bg-surface';
const waterfallKey = (entityId: string, predicateId: string) => `${entityId}:${predicateId}`;
const arrow = (sorts: SortKey[], key: string | null) => {
  const s = sorts.find((x) => x.key === key);
  return s ? (s.dir === 'asc' ? ' ↑' : ' ↓') : '';
};

export function AccountsTable({
  ws,
  rows,
  columns,
  sorts,
  sortHrefs,
  canEdit = false,
  briefId = null,
  filter = null,
  waterfallAttempts = {},
}: {
  ws: string;
  rows: TableRow[];
  columns: Column[];
  sorts: SortKey[];
  /** The URL each sortable header links to, keyed by sort key (system sort or column key). */
  sortHrefs: Record<string, string>;
  /** Editor+: column menus and bulk Run. A Viewer sees neither. */
  canEdit?: boolean;
  /** The lens Brief (null under All Briefs): the runs' Brief and budget. */
  briefId?: string | null;
  /** The view's filter AST: a column menu runs on the accounts in view. */
  filter?: Record<string, unknown> | null;
  /** One waterfall's last attempts per row, keyed `<entity_id>:<predicate_id>`. */
  waterfallAttempts?: Record<string, WaterfallAttempt[]>;
}) {
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [active, setActive] = useState<{ r: number; c: number }>({ r: 0, c: 0 });
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [drawer, setDrawer] = useState(false);
  const [confirm, setConfirm] = useState<{ title: string; jobs: RunJob[] } | null>(null);
  const gridRef = useRef<HTMLTableElement>(null);
  const hidden = columns.slice(MID_COLUMNS);

  const toggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }, []);

  const toggleWaterfall = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  };

  const open = (row: TableRow, col: Column) =>
    window.dispatchEvent(
      new CustomEvent<OpenCellDetail>(OPEN_CELL_EVENT, { detail: { accountId: row.account_id, entityId: row.entity_id, predicateId: col.id } }),
    );

  const focusCell = (r: number, c: number) => {
    const el = gridRef.current?.querySelector<HTMLElement>(`[data-r="${r}"][data-c="${c}"]`);
    // A column hidden by the breakpoint has no box: it cannot take focus.
    if (!el || el.offsetParent === null) return false;
    el.focus();
    return true;
  };

  function onKeyDown(e: KeyboardEvent<HTMLTableElement>) {
    const cell = (e.target as HTMLElement).closest<HTMLElement>('[data-r]');
    if (!cell || e.target !== cell) return; // keys inside a link or checkbox keep their own meaning
    const r = Number(cell.dataset.r);
    const c = Number(cell.dataset.c);
    const lastC = SYSTEM_COUNT + columns.length - 1;
    const step = (dr: number, dc: number) => {
      const nr = r + dr;
      if (nr < 0 || nr >= rows.length) return;
      // Sideways, skip columns the breakpoint hides; up and down stay in the column.
      for (let nc = c + dc; nc >= 0 && nc <= lastC; nc += dc || 1) {
        if (focusCell(nr, nc) || dc === 0) return;
      }
    };
    switch (e.key) {
      case 'ArrowRight': e.preventDefault(); step(0, 1); break;
      case 'ArrowLeft': e.preventDefault(); step(0, -1); break;
      case 'ArrowDown': e.preventDefault(); step(1, 0); break;
      case 'ArrowUp': e.preventDefault(); step(-1, 0); break;
      case ' ': e.preventDefault(); toggle(rows[r].account_id); break;
      // Enter opens the panel. It never runs anything.
      case 'Enter':
        if (c >= SYSTEM_COUNT) { e.preventDefault(); open(rows[r], columns[c - SYSTEM_COUNT]); }
        break;
      default:
    }
  }

  // data-ready: React has taken the table over, so keys and clicks are handled (the e2e waits for it).
  return (
    <div data-table="" ref={(el) => el?.setAttribute('data-ready', '')} className="max-md:hidden">
      {selected.size > 0 ? (
        <BulkBar
          rows={rows.filter((r) => selected.has(r.account_id))}
          columns={columns}
          canEdit={canEdit}
          briefId={briefId}
          onRun={(title, jobs) => setConfirm({ title, jobs })}
          onClear={() => setSelected(new Set())}
        />
      ) : null}
      {confirm ? <RunConfirm ws={ws} title={confirm.title} jobs={confirm.jobs} onClose={() => setConfirm(null)} /> : null}
      {hidden.length > 0 ? (
        <div className="mb-2 hidden justify-end md:max-lg:flex">
          <button type="button" className="si-btn si-btn-sm" aria-haspopup="dialog" onClick={() => setDrawer(true)}>
            +{hidden.length} columns
          </button>
        </div>
      ) : null}
      <div className="si-table-box rounded-md border border-line bg-surface">
        <table ref={gridRef} className="si-table" role="grid" aria-label="Accounts" onKeyDown={onKeyDown}>
          <thead>
            <tr>
              {SYSTEM.map((s, i) => (
                <th key={s.id} scope="col" className={i === 0 ? 'sticky left-0 z-[1] min-w-[220px] bg-[#fafafa]' : ''} aria-sort={ariaSort(sorts, s.sort)}>
                  <Head label={s.label} sortKey={s.sort} sorts={sorts} hrefs={sortHrefs} />
                </th>
              ))}
              {columns.map((c, i) => (
                <th key={c.id} scope="col" data-column={c.key} className={i >= MID_COLUMNS ? 'max-lg:hidden' : ''} aria-sort={ariaSort(sorts, c.key)}>
                  <span className="inline-flex items-center whitespace-nowrap">
                    <Head label={c.label} sortKey={c.key} sorts={sorts} hrefs={sortHrefs} />
                    {canEdit ? (
                      <ColumnMenu
                        ws={ws}
                        column={c}
                        onRun={(mode) =>
                          setConfirm({
                            title: `Run ${mode === 'errored' ? 'errored' : mode} · ${c.label}`,
                            jobs: [{ predicate: c.id, label: c.label, mode, brief: briefId, filter }],
                          })
                        }
                      />
                    ) : null}
                    {c.fill_method === 'provider' ? (
                      <button
                        type="button"
                        className="ml-1 rounded-sm border border-line px-1 text-[11px] text-ink-muted"
                        aria-label={`${expanded.has(c.id) ? 'Collapse' : 'Expand'} ${c.label} providers`}
                        aria-expanded={expanded.has(c.id)}
                        onClick={() => toggleWaterfall(c.id)}
                      >
                        {expanded.has(c.id) ? '−' : '+'} providers
                      </button>
                    ) : null}
                  </span>
                  {c.fill_method === 'provider' && expanded.has(c.id) ? <WaterfallSubcolumns header /> : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => {
              const verdict = Verdict.safeParse(r.verdict);
              const band = Band.safeParse(r.band);
              const stage = Stage.safeParse(r.stage);
              const isSel = selected.has(r.account_id);
              const cellProps = (c: number) => ({
                'data-r': ri,
                'data-c': c,
                tabIndex: active.r === ri && active.c === c ? 0 : -1,
                onFocus: () => setActive({ r: ri, c }),
              });
              return (
                <tr key={r.account_id} data-row={r.name} aria-selected={isSel} className={isSel ? '[&>td]:bg-signal-soft' : ''}>
                  <td {...cellProps(0)} className={`${sticky} ${isSel ? '!bg-signal-soft' : ''} min-w-[220px]`}>
                    <div className="flex items-start gap-2">
                      <input
                        type="checkbox"
                        aria-label={`Select ${r.name}`}
                        checked={isSel}
                        onChange={() => toggle(r.account_id)}
                        className="mt-1 h-4 w-4 flex-none accent-[var(--ink)]"
                      />
                      <div className="min-w-0">
                        <Link href={`/workspace/${ws}/accounts/${r.account_id}`} className="block max-w-[28ch] truncate font-medium text-ink no-underline hover:underline">
                          {r.name}
                        </Link>
                        {r.country ? <span className="block text-[12px] text-ink-muted">{r.country}</span> : null}
                      </div>
                    </div>
                  </td>
                  <td {...cellProps(1)}>{verdict.success ? <VerdictBadge verdict={verdict.data} /> : <Dash />}</td>
                  <td {...cellProps(2)}>
                    <span className="inline-flex items-center gap-2">
                      {r.priority != null ? <span className="tabular-nums">{Math.round(r.priority)}</span> : null}
                      {band.success ? <BandChip band={band.data} /> : r.priority == null ? <Dash /> : null}
                    </span>
                  </td>
                  <td {...cellProps(3)}>{stage.success ? <StageBadge stage={stage.data} /> : r.stage}</td>
                  <td {...cellProps(4)}>{r.owner ? <span className="block max-w-[18ch] truncate" title={r.owner.name}>{r.owner.name}</span> : <Dash />}</td>
                  <td {...cellProps(5)} className="whitespace-nowrap">{formatDate(r.last_signal) ?? <Dash />}</td>
                  {columns.map((c, ci) => (
                    <td
                      key={c.id}
                      {...cellProps(SYSTEM_COUNT + ci)}
                      data-column={c.key}
                      className={`cursor-pointer ${ci >= MID_COLUMNS ? 'max-lg:hidden' : ''}`}
                      onClick={() => open(r, c)}
                    >
                      <span className="flex flex-col gap-1">
                        <Cell cell={r.cells[c.id]} column={c} />
                        {c.fill_method === 'provider' && expanded.has(c.id) ? (
                          <WaterfallSubcolumns attempts={waterfallAttempts[waterfallKey(r.entity_id, c.id)] ?? []} />
                        ) : null}
                      </span>
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {drawer ? (
        <div className="fixed inset-0 z-30 flex justify-end bg-black/20" onClick={() => setDrawer(false)}>
          <aside
            role="dialog"
            aria-modal="true"
            aria-label="More columns"
            className="h-full w-[min(420px,100%)] overflow-y-auto border-l border-line bg-surface p-5 shadow-lg"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.key === 'Escape' && setDrawer(false)}
          >
            <div className="mb-3 flex items-center justify-between">
              <h2 className="si-h2 m-0">More columns</h2>
              <button type="button" className="si-btn si-btn-quiet si-btn-sm" onClick={() => setDrawer(false)} autoFocus>
                Close
              </button>
            </div>
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {rows.map((r) => (
                <li key={r.account_id} className="rounded-md border border-line p-3">
                  <p className="m-0 mb-2 truncate text-[14px] font-medium text-ink">{r.name}</p>
                  <dl className="m-0 grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1.5 text-[13px]">
                    {hidden.map((c) => (
                      <div key={c.id} className="contents">
                        <dt className="truncate text-ink-muted">{c.label}</dt>
                        <dd className="m-0 min-w-0"><Cell cell={r.cells[c.id]} column={c} /></dd>
                      </div>
                    ))}
                  </dl>
                </li>
              ))}
            </ul>
          </aside>
        </div>
      ) : null}
    </div>
  );
}

const Dash = () => <span className="text-ink-faint" aria-label="Empty">—</span>;

function ariaSort(sorts: SortKey[], key: string | null): 'ascending' | 'descending' | undefined {
  const s = sorts.find((x) => x.key === key);
  return s ? (s.dir === 'asc' ? 'ascending' : 'descending') : undefined;
}

function Head({ label, sortKey, sorts, hrefs }: { label: string; sortKey: string | null; sorts: SortKey[]; hrefs: Record<string, string> }) {
  const href = sortKey ? hrefs[sortKey] : undefined;
  if (!href) return <>{label}</>;
  return (
    <Link href={href} className="text-inherit no-underline hover:underline" title={`Sort by ${label}`}>
      {label}
      {arrow(sorts, sortKey)}
    </Link>
  );
}
