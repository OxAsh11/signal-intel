import Link from 'next/link';

const box = 'rounded-md border border-line bg-surface px-5 py-6 text-[14px] text-ink-body';
const nf = new Intl.NumberFormat('en');

/** The `f` or sort in the URL could not be read (decode failure, or the server refused it with 22023). */
export function BadFilter({ clearHref, detail }: { clearHref: string; detail?: string }) {
  return (
    <div className={box} role="alert" data-table-state="bad-filter">
      <p className="m-0 font-medium text-ink">This filter couldn&apos;t be read</p>
      {detail ? <p className="mt-1 text-[13px] text-ink-muted">{detail}</p> : null}
      <Link href={clearHref} className="si-btn si-btn-sm mt-3 no-underline">
        Clear filter
      </Link>
    </div>
  );
}

/** No accounts in the workspace yet: say what the sources are doing. */
export function NoAccounts({ active, radars, records }: { active: number; radars: number; records: number }) {
  return (
    <div className={box} data-table-state="no-accounts">
      <p className="m-0 font-medium text-ink">No accounts yet</p>
      <p className="mt-1 text-[14px]">
        <Link href="/sources" className="text-ink underline">
          Sources are running: {nf.format(active)} of {nf.format(radars)} active, {nf.format(records)} records fetched
        </Link>
      </p>
    </div>
  );
}

export function NoMatch({ clearHref }: { clearHref: string }) {
  return (
    <div className={box} data-table-state="no-match">
      <p className="m-0 font-medium text-ink">No accounts match</p>
      <Link href={clearHref} className="si-btn si-btn-sm mt-3 no-underline">
        Clear filter
      </Link>
    </div>
  );
}

export function PastLastPage({ href }: { href: string }) {
  return (
    <div className={box} data-table-state="past-end">
      <p className="m-0 font-medium text-ink">That page is past the last one</p>
      <Link href={href} className="si-btn si-btn-sm mt-3 no-underline">
        Back to page 1
      </Link>
    </div>
  );
}

/** The read failed: an inline banner, never an error page. A cell error stays in its cell. */
export function TableError({ retryHref, message }: { retryHref: string; message?: string }) {
  return (
    <div className="rounded-md border border-danger bg-danger-soft px-5 py-4 text-[14px] text-danger" role="alert" data-table-state="error">
      <p className="m-0 font-medium">The accounts could not load{message ? `: ${message}` : ''}</p>
      <Link href={retryHref} className="si-btn si-btn-sm mt-3 no-underline">
        Retry
      </Link>
    </div>
  );
}

const HEADS = ['Account', 'Verdict', 'Score', 'Stage', 'Owner', 'Last signal'];

/** Skeleton rows under a visible header, while the page loads. */
export function TableSkeleton() {
  return (
    <div className="si-table-box rounded-md border border-line bg-surface" role="status" aria-busy="true" aria-label="Accounts, loading">
      <table className="si-table">
        <thead>
          <tr>
            {HEADS.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: 8 }, (_, i) => (
            <tr key={i}>
              {HEADS.map((h, j) => (
                <td key={h}>
                  <span className="block h-3 animate-pulse rounded-[2px] bg-surface-sunken" style={{ width: j === 0 ? '70%' : '50%' }} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
