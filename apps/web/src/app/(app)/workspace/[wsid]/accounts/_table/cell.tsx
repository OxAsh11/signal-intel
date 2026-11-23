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

/** The last waterfall attempt for one cell, from public.waterfall_attempts. */
export type WaterfallAttempt = {
  provider: string;
  status: 'found' | 'not_found' | 'error' | string;
  cost_usd: number | string | null;
  ordinal: number;
};
export const WATERFALL_PROVIDERS = ['leadmagic', 'findymail', 'prospeo'];

const attemptStatus = (attempt: WaterfallAttempt | undefined) => {
  if (!attempt) return { mark: 'Skipped', label: 'Skipped' };
  if (attempt.status === 'found') return { mark: '✓', label: 'Found' };
  return { mark: '✗', label: attempt.status === 'error' ? 'Error' : 'Not found' };
};

/**
 * A waterfall column expanded into one sub-column per provider. The header shows the vendors; a
 * cell shows ✓ / ✗ / Skipped from that row's last run. Attempts are read-only evidence — clicking a
 * sub-column still opens the cell panel, where the cost and error kind are listed.
 */
export function WaterfallSubcolumns({
  attempts = [],
  header = false,
}: {
  attempts?: WaterfallAttempt[];
  header?: boolean;
}) {
  const byProvider = new Map(attempts.map((a) => [a.provider, a]));
  const providers = [...WATERFALL_PROVIDERS, ...attempts.map((a) => a.provider).filter((p) => !WATERFALL_PROVIDERS.includes(p))];
  return (
    <span className="grid min-w-[168px] grid-cols-3 gap-1" data-waterfall-subcolumns="" data-waterfall-header={header ? '' : undefined}>
      {providers.slice(0, 3).map((name) => {
        const attempt = byProvider.get(name);
        const state = attemptStatus(attempt);
        const cost = attempt?.cost_usd == null ? null : Number(attempt.cost_usd);
        return (
          <span
            key={name}
            className="inline-flex min-w-0 items-center justify-center gap-1 rounded-sm border border-line px-1 py-0.5 text-[11px] text-ink-muted"
            data-waterfall-provider={name}
            data-waterfall-status={attempt?.status ?? 'skipped'}
            title={header ? name : `${name}: ${state.label}${cost != null ? ` · $${cost.toFixed(3)}` : ''}`}
          >
            {header ? <span className="truncate">{name}</span> : <span aria-label={state.label}>{state.mark}</span>}
          </span>
        );
      })}
    </span>
  );
}

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
