'use client';

import Link from 'next/link';
import { Cell, formatDate } from '../accounts/_table/cell';
import type { Column } from '../accounts/_table/types';
import type { PersonRow } from './types';

/** The panel listens for this: the email cell gives it the mask even when the RPC refuses a Viewer. */
export const OPEN_PERSON_EVENT = 'si:open-person';
export type OpenPersonDetail = { personId: string; name: string; masked: string };

const SYSTEM = ['Name', 'Role', 'Account', 'Work email', 'LinkedIn', 'Location flag', 'Last touch'] as const;
const Dash = () => <span className="text-ink-faint" aria-label="Empty">—</span>;

function openEmail(row: PersonRow) {
  window.dispatchEvent(
    new CustomEvent<OpenPersonDetail>(OPEN_PERSON_EVENT, {
      detail: { personId: row.entity_id, name: row.name, masked: row.work_email ?? '—' },
    }),
  );
}

function EmailValue({ row }: { row: PersonRow }) {
  if (!row.work_email) return <Dash />;
  return (
    <button
      type="button"
      className="max-w-[24ch] truncate text-left text-ink underline decoration-dotted underline-offset-2 hover:text-signal-strong"
      title="Open person details"
      onClick={() => openEmail(row)}
    >
      {row.work_email}
    </button>
  );
}

function LocationFlag({ row }: { row: PersonRow }) {
  if (!row.location_mismatch) return <Dash />;
  return (
    <span className="si-chip" title="The person may work at a different office">
      Possibly a different office
    </span>
  );
}

/** Desktop operating table: seven person columns, then any person-scoped predicate columns. */
export function PeopleTable({ ws, rows, columns }: { ws: string; rows: PersonRow[]; columns: Column[] }) {
  return (
    <div data-table="" className="max-md:hidden">
      <div className="si-table-box rounded-md border border-line bg-surface">
        <table className="si-table" role="grid" aria-label="People">
          <thead>
            <tr>
              {SYSTEM.map((h) => <th key={h} scope="col">{h}</th>)}
              {columns.map((c) => <th key={c.id} scope="col" data-column={c.key}>{c.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.entity_id} data-row={r.name}>
                <td className="min-w-[200px] font-medium text-ink">{r.name}</td>
                <td>{r.title ?? <Dash />}</td>
                <td data-column="account">
                  {r.account_id && r.account_name ? (
                    <Link href={`/workspace/${ws}/accounts/${r.account_id}`} className="block max-w-[22ch] truncate text-ink no-underline hover:underline">
                      {r.account_name}
                    </Link>
                  ) : (
                    <Dash />
                  )}
                  {r.country ? <span className="block text-[12px] text-ink-muted">{r.country}</span> : null}
                </td>
                <td data-column="work_email"><EmailValue row={r} /></td>
                <td data-column="linkedin">
                  {r.linkedin ? (
                    <a href={r.linkedin} target="_blank" rel="noreferrer" className="block max-w-[22ch] truncate text-ink no-underline hover:underline">
                      {r.linkedin.replace(/^https?:\/\/(www\.)?/, '')}
                    </a>
                  ) : <Dash />}
                </td>
                <td data-column="location_flag"><LocationFlag row={r} /></td>
                <td className="whitespace-nowrap">{formatDate(r.last_signal) ?? <Dash />}</td>
                {columns.map((c) => (
                  <td key={c.id} data-column={c.key}><Cell cell={r.cells[c.id]} column={c} /></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Phone (<768 px): one card per person, with no sideways scrolling. */
export function PeopleCards({ ws, rows, columns }: { ws: string; rows: PersonRow[]; columns: Column[] }) {
  const extra = columns.slice(0, 2);
  return (
    <ul data-person-cards="" className="m-0 flex list-none flex-col gap-3 p-0 md:hidden">
      {rows.map((r) => (
        <li key={r.entity_id} data-row={r.name} className="min-w-0 rounded-md border border-line bg-surface p-4">
          <p className="m-0 truncate text-[15px] font-medium text-ink">{r.name}</p>
          {r.title ? <p className="m-0 text-[13px] text-ink-muted">{r.title}</p> : null}
          <dl className="m-0 mt-3 grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-1.5 text-[13px]">
            <dt className="text-ink-muted">Account</dt>
            <dd className="m-0 min-w-0">
              {r.account_name ? (
                <Link href={`/workspace/${ws}/accounts/${r.account_id}`} className="block truncate text-ink no-underline">
                  {r.account_name}
                </Link>
              ) : <Dash />}
            </dd>
            <dt className="text-ink-muted">Work email</dt>
            <dd className="m-0 min-w-0"><EmailValue row={r} /></dd>
            <dt className="text-ink-muted">Location</dt>
            <dd className="m-0 min-w-0"><LocationFlag row={r} /></dd>
            <dt className="text-ink-muted">Last touch</dt>
            <dd className="m-0 min-w-0">{formatDate(r.last_signal) ?? <Dash />}</dd>
            {extra.map((c) => (
              <div key={c.id} className="contents">
                <dt className="truncate text-ink-muted">{c.label}</dt>
                <dd className="m-0 min-w-0"><Cell cell={r.cells[c.id]} column={c} /></dd>
              </div>
            ))}
          </dl>
        </li>
      ))}
    </ul>
  );
}
