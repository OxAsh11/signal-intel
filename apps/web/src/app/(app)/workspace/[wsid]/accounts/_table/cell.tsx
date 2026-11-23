import type { ReactNode } from 'react';
import type { CellData, Column } from './types';

const nf = new Intl.NumberFormat('en');
const dateFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

/** `d MMM yyyy`, from a date or a timestamp; null when it is not one. */
export function formatDate(v: unknown): string | null {
  if (typeof v !== 'string') return null;
  const t = Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(v) ? `${v}T00:00:00Z` : v);
  return Number.isNaN(t) ? null : dateFmt.format(t);
}

/** A claim value as the cell shows it: Yes/No, 1,234, 5 Dec 2026, or the text as is. */
export function formatValue(value: unknown, type: string): string {
  if (value === null || value === undefined) return '—';
  if (type === 'boolean' && typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (type === 'number' && typeof value === 'number') return nf.format(value);
  if (type === 'date') return formatDate(value) ?? String(value);
  return typeof value === 'string' ? value : JSON.stringify(value);
}

export const confidenceBand = (c: number): 'H' | 'M' | 'L' => (c >= 80 ? 'H' : c >= 50 ? 'M' : 'L');

function Value({ text }: { text: string }) {
  return (
    <span className="block max-w-[28ch] truncate" title={text}>
      {text}
    </span>
  );
}

/** The nine P3 cell states, as 05 §3.1 draws them. Pure markup: safe on the server and the client. */
export function Cell({ cell, column }: { cell: CellData | undefined; column: Pick<Column, 'value_type'> }) {
  const state = cell?.state ?? 'empty';
  const text = formatValue(cell?.value, column.value_type);
  let body: ReactNode;
  switch (state) {
    case 'queued':
      body = <span role="img" aria-label="Queued" title="Queued" className="inline-block h-2 w-2 rounded-full bg-line-strong" />;
      break;
    case 'running':
      body = (
        <span role="img" aria-label="Running" title="Running" className="block h-1.5 w-16 overflow-hidden rounded-full bg-surface-sunken">
          <span className="block h-full w-1/2 animate-pulse rounded-full bg-signal" />
        </span>
      );
      break;
    case 'value':
    case 'stale':
      body = (
        <span className="inline-flex min-w-0 items-center gap-1.5">
          <Value text={text} />
          {cell?.confidence != null ? (
            <span
              title={`Confidence ${cell.confidence}`}
              className="inline-flex flex-none rounded-[4px] border border-line px-1 text-[11px] font-medium leading-4 text-ink-muted"
            >
              {confidenceBand(cell.confidence)}
            </span>
          ) : null}
          {state === 'stale' ? (
            <>
              <span
                aria-hidden="true"
                title="Stale: the inputs changed since this ran"
                className="absolute -right-3 -top-2 h-0 w-0 border-l-[10px] border-t-[10px] border-l-transparent border-t-marker"
              />
              <span className="sr-only">Stale</span>
            </>
          ) : null}
        </span>
      );
      break;
    case 'derived':
      body = (
        <span className="si-derived-body" style={{ padding: '2px 8px' }}>
          <span className="si-derived-val">
            <Value text={text} />
          </span>
        </span>
      );
      break;
    case 'no_data':
      body = <span className="text-ink-faint">No data</span>;
      break;
    case 'skipped':
      body = <span className="text-ink-faint">Skipped</span>;
      break;
    case 'error':
      body = (
        <span className="inline-flex min-w-0 items-center gap-1.5 text-danger">
          <span aria-hidden="true" className="h-2 w-2 flex-none rounded-full bg-danger" />
          <span className="block max-w-[20ch] truncate" title={cell?.error ?? 'Error'}>
            {cell?.error ?? 'Error'}
          </span>
        </span>
      );
      break;
    default:
      body = (
        <span className="text-ink-faint" aria-label="Empty">
          —
        </span>
      );
  }
  return (
    <span className="relative inline-flex max-w-full items-center" data-state={state}>
      {body}
    </span>
  );
}
