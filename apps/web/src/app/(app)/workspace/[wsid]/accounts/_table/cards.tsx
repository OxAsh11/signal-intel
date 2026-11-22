import Link from 'next/link';
import { BandChip, StageBadge, VerdictBadge } from '@signal/ui';
import { Band, Stage, Verdict } from '@signal/contracts';
import { Cell } from './cell';
import type { Column, TableRow } from './types';

/** One account as a card: name, country, verdict, band, stage and the first two column values. */
export function AccountCard({ ws, row: r, columns }: { ws: string; row: TableRow; columns: Column[] }) {
  const top = columns.slice(0, 2);
  const verdict = Verdict.safeParse(r.verdict);
  const band = Band.safeParse(r.band);
  const stage = Stage.safeParse(r.stage);
  return (
    <li data-row={r.name} className="min-w-0 rounded-md border border-line bg-surface p-4">
      <Link href={`/workspace/${ws}/accounts/${r.account_id}`} className="block truncate text-[15px] font-medium text-ink no-underline">
        {r.name}
      </Link>
      {r.country ? <p className="m-0 text-[12px] text-ink-muted">{r.country}</p> : null}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {verdict.success ? <VerdictBadge verdict={verdict.data} /> : null}
        {band.success ? <BandChip band={band.data} /> : null}
        {stage.success ? <StageBadge stage={stage.data} /> : null}
      </div>
      {top.length ? (
        <dl className="m-0 mt-3 grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1.5 text-[13px]">
          {top.map((c) => (
            <div key={c.id} className="contents">
              <dt className="truncate text-ink-muted">{c.label}</dt>
              <dd className="m-0 min-w-0">
                <Cell cell={r.cells[c.id]} column={c} />
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </li>
  );
}

/** Phone (< 768 px): one card per account. */
export function Cards({ ws, rows, columns }: { ws: string; rows: TableRow[]; columns: Column[] }) {
  return (
    <ul className="m-0 flex list-none flex-col gap-3 p-0 md:hidden" data-cards="">
      {rows.map((r) => (
        <AccountCard key={r.account_id} ws={ws} row={r} columns={columns} />
      ))}
    </ul>
  );
}
