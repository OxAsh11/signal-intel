import type { Metadata } from 'next';
import Link from 'next/link';
import { Suspense } from 'react';
import { getBriefLens } from '@/lib/brief-lens';
import { atLeast, getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';
import { Cards } from './_table/cards';
import { BadFilter, NoAccounts, NoMatch, PastLastPage, TableError, TableSkeleton } from './_table/states';
import { CellPanel } from './_table/panel';
import { AccountsTable } from './_table/table';
import type { TableData } from './_table/types';
import {
  PAGE_SIZE,
  decodeFilter,
  formatSort,
  nextSort,
  parseSort,
  readQuery,
  sortAst,
  sortKeysFromAst,
  tableHref,
  type SortKey,
  type TableQuery,
} from './_table/url';

export const metadata: Metadata = { title: 'Accounts' };

type SavedView = { id: string | null; preset: string | null; filter: unknown; sort: unknown; columns: unknown };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const nf = new Intl.NumberFormat('en');
const isEmptyFilter = (f: unknown) => f == null || (typeof f === 'object' && Object.keys(f as object).length === 0);

/**
 * The accounts operating table. The URL is the state (see _table/url.ts); a page reads only its 50
 * rows' cells (public.account_table). A filter that cannot be read, in the URL or by the server,
 * shows "This filter couldn't be read" with Clear filter, never an error page.
 */
export default async function AccountsPage({ params, searchParams }: PageProps<'/workspace/[wsid]/accounts'>) {
  const [{ wsid }, sp] = await Promise.all([params, searchParams]);
  return (
    <div className="flex flex-col gap-4">
      <h1 className="si-h1">Accounts</h1>
      <Suspense fallback={<TableSkeleton />}>
        <TableSection ws={wsid} sp={sp} />
      </Suspense>
    </div>
  );
}

async function TableSection({ ws, sp }: { ws: string; sp: Record<string, string | string[] | undefined> }) {
  const q = readQuery(sp);
  const here = tableHref(ws, q, { page: q.page });
  // Clear filter: no `f`, no view filter, no page; the sort stays unless it is the unreadable part.
  const clear = (dropSort: boolean) => tableHref(ws, q, { f: null, view: null, page: 1, ...(dropSort ? { sort: null } : {}) });

  // The URL's own filter and sort must parse before anything is asked of the server.
  let urlFilter: Record<string, unknown> | null = null;
  if (q.f !== undefined) {
    const d = decodeFilter(q.f);
    if (!d.ok) return <BadFilter clearHref={clear(false)} />;
    urlFilter = d.ast;
  }
  const urlSort = parseSort(q.sort);
  if (urlSort === null) return <BadFilter clearHref={clear(true)} detail="The sort is not valid." />;

  const supabase = await createClient();
  if (!supabase) return <TableError retryHref={here} />;
  const lens = await getBriefLens(sp);

  // A view supplies the filter (unless `f` replaces it), the sort (unless `sort` does) and the columns.
  let view: SavedView | null = null;
  if (q.view) {
    const { data } = await supabase.rpc('list_views', { p_ws: ws, p_brief: lens.briefId });
    view = ((data ?? []) as SavedView[]).find((v) => v.preset === q.view || v.id === q.view) ?? null;
  }
  const filter = urlFilter ?? (view && !isEmptyFilter(view.filter) ? (view.filter as Record<string, unknown>) : {});
  const sorts: SortKey[] = q.sort ? urlSort : sortKeysFromAst(view?.sort);
  const columnIds = view ? await viewColumnIds(supabase, ws, view.columns) : null;
  const filtered = !isEmptyFilter(filter);

  const { data, error } = await supabase.rpc('account_table', {
    p_ws: ws,
    p_brief: lens.briefId,
    p_filter: filter,
    p_sort: sortAst(sorts),
    p_columns: columnIds,
    p_limit: PAGE_SIZE,
    p_offset: (q.page - 1) * PAGE_SIZE,
  });
  if (error) {
    if (error.code === '22023') return <BadFilter clearHref={clear(false)} detail={error.message} />;
    console.error(`accounts: account_table failed for workspace ${ws}: ${error.message}`);
    return <TableError retryHref={here} message={error.message} />;
  }
  const table = data as TableData | null;
  if (!table) return <TableError retryHref={here} />;

  if (table.total === 0) {
    if (filtered) return <NoMatch clearHref={clear(false)} />;
    const count = async (build: (qb: ReturnType<typeof radarCount>) => PromiseLike<{ count: number | null }>) => (await build(radarCount(supabase, ws))).count ?? 0;
    const [radars, active, records] = await Promise.all([
      count((b) => b),
      count((b) => b.eq('status', 'active')),
      Promise.resolve(supabase.from('source_record').select('id', { count: 'exact', head: true }).eq('workspace_id', ws)).then((r) => r.count ?? 0),
    ]);
    return <NoAccounts radars={radars} active={active} records={records} />;
  }
  if (table.rows.length === 0) return <PastLastPage href={tableHref(ws, q, { page: 1 })} />;

  const sortHrefs: Record<string, string> = {};
  for (const key of ['name', 'verdict', 'priority', ...table.columns.map((c) => c.key)]) {
    const next = nextSort(sorts, key);
    sortHrefs[key] = tableHref(ws, q, { sort: next.length ? formatSort(next) : null, page: 1 });
  }

  const ctx = await getOrgContext();
  const canEdit = atLeast(ctx?.workspaceRole, 'Editor');
  const userName = ctx?.user.name ?? ctx?.user.email ?? 'you';
  const pages = Math.ceil(table.total / PAGE_SIZE);
  const from = (q.page - 1) * PAGE_SIZE + 1;
  return (
    <>
      <AccountsTable ws={ws} rows={table.rows} columns={table.columns} sorts={sorts} sortHrefs={sortHrefs} />
      <CellPanel ws={ws} briefId={lens.briefId} canEdit={canEdit} userName={userName} />
      <Cards ws={ws} rows={table.rows} columns={table.columns} />
      <Pager ws={ws} q={q} pages={pages} from={from} to={from + table.rows.length - 1} total={table.total} />
    </>
  );
}

const radarCount = (supabase: NonNullable<Awaited<ReturnType<typeof createClient>>>, ws: string) =>
  supabase.from('radar').select('id', { count: 'exact', head: true }).eq('workspace_id', ws);

/** A saved view's predicate columns, as ids (a ref may be an id or a key). Null: the lens Brief's columns. */
async function viewColumnIds(supabase: NonNullable<Awaited<ReturnType<typeof createClient>>>, ws: string, columns: unknown): Promise<string[] | null> {
  if (!Array.isArray(columns)) return null;
  const refs = columns
    .filter((c): c is { kind: string; ref: string } => c !== null && typeof c === 'object' && (c as { kind?: unknown }).kind === 'predicate' && typeof (c as { ref?: unknown }).ref === 'string')
    .map((c) => c.ref);
  if (refs.length === 0) return null;
  const keys = refs.filter((r) => !UUID.test(r));
  const byKey = new Map<string, string>();
  if (keys.length) {
    const { data } = await supabase.from('predicate').select('id, key, workspace_id').in('key', keys).or(`workspace_id.eq.${ws},workspace_id.is.null`);
    // A workspace column wins over a global one of the same key.
    for (const p of (data ?? []) as { id: string; key: string; workspace_id: string | null }[]) {
      if (p.workspace_id || !byKey.has(p.key)) byKey.set(p.key, p.id);
    }
  }
  const ids = refs.map((r) => (UUID.test(r) ? r : byKey.get(r))).filter((x): x is string => !!x);
  return ids.length ? ids : null;
}

function Pager({ ws, q, pages, from, to, total }: { ws: string; q: TableQuery; pages: number; from: number; to: number; total: number }) {
  return (
    <nav className="flex items-center justify-between gap-3 text-[13px] text-ink-muted" aria-label="Pages" data-pager="">
      <span>
        {nf.format(from)}–{nf.format(to)} of {nf.format(total)}
      </span>
      <span className="flex items-center gap-2">
        {q.page > 1 ? (
          <Link className="si-btn si-btn-sm no-underline" href={tableHref(ws, q, { page: q.page - 1 })}>
            Prev
          </Link>
        ) : (
          <span className="si-btn si-btn-sm" aria-disabled="true" style={{ opacity: 0.5 }}>Prev</span>
        )}
        {q.page < pages ? (
          <Link className="si-btn si-btn-sm no-underline" href={tableHref(ws, q, { page: q.page + 1 })}>
            Next
          </Link>
        ) : (
          <span className="si-btn si-btn-sm" aria-disabled="true" style={{ opacity: 0.5 }}>Next</span>
        )}
      </span>
    </nav>
  );
}
