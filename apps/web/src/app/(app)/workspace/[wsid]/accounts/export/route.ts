import { getBriefLens } from '@/lib/brief-lens';
import { createClient } from '@/lib/supabase/server';
import { formatDate, formatValue } from '../_table/cell';
import { UUID, viewColumnIds } from '../_table/server';
import type { Column, TableData, TableRow } from '../_table/types';
import { decodeFilter, parseSort, sortAst, sortKeysFromAst } from '../_table/url';

const PAGE = 200;
const MAX_ROWS = 10_000;

/** RFC 4180 quoting; a cell a spreadsheet would run as a formula (= + - @) gets a leading '. */
function csvCell(v: string): string {
  const safe = /^[=+\-@]/.test(v) ? `'${v}` : v;
  return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

function cellText(row: TableRow, c: Column): string {
  const cell = row.cells[c.id];
  switch (cell?.state) {
    case 'value':
    case 'derived':
    case 'stale':
      return formatValue(cell.value, c.value_type);
    case 'no_data':
      return 'No data';
    case 'skipped':
      return 'Skipped';
    case 'error':
      return `Error: ${cell.error ?? 'error'}`;
    default:
      return '';
  }
}

const isEmpty = (f: unknown) => f == null || (typeof f === 'object' && Object.keys(f as object).length === 0);
const text = (status: number, body: string) => new Response(body, { status, headers: { 'content-type': 'text/plain; charset=utf-8' } });

/**
 * GET /workspace/<ws>/accounts/export?brief&view&f&sort[&evidence=1]: the current view as CSV, up to
 * 10,000 rows, read 200 at a time through account_table (whose own role check is the gate: a
 * non-member gets 403). The first page is read before the response starts, so a bad filter or a
 * non-member is a status code; later pages stream. With evidence=1 each column gets a
 * "<label> source" column holding the source URL of the claim the cell shows (one claim lookup
 * per 100 cells, never one per cell).
 */
export async function GET(request: Request, { params }: { params: Promise<{ wsid: string }> }) {
  const { wsid: ws } = await params;
  if (!UUID.test(ws)) return text(404, 'Not found');
  const url = new URL(request.url);
  const sp: Record<string, string> = {};
  for (const [k, v] of url.searchParams) if (!(k in sp)) sp[k] = v;
  const evidence = sp.evidence === '1';

  const supabase = await createClient();
  if (!supabase) return text(503, 'Export is unavailable');
  const lens = await getBriefLens(sp);

  let urlFilter: Record<string, unknown> | null = null;
  if (sp.f !== undefined && sp.f !== '') {
    const d = decodeFilter(sp.f);
    if (!d.ok) return text(400, "This filter couldn't be read");
    urlFilter = d.ast;
  }
  const urlSort = parseSort(sp.sort || undefined);
  if (urlSort === null) return text(400, 'The sort is not valid');

  let view: { filter: unknown; sort: unknown; columns: unknown } | null = null;
  if (sp.view) {
    const { data, error } = await supabase.rpc('list_views', { p_ws: ws, p_brief: lens.briefId });
    if (error) return error.code === '42501' ? text(403, 'Forbidden') : text(500, 'Export failed');
    view = ((data ?? []) as { id: string | null; preset: string | null; filter: unknown; sort: unknown; columns: unknown }[]).find((v) => v.preset === sp.view || v.id === sp.view) ?? null;
  }
  const filter = urlFilter ?? (view && !isEmpty(view.filter) ? (view.filter as Record<string, unknown>) : {});
  const sorts = sp.sort ? urlSort : sortKeysFromAst(view?.sort);
  const columnIds = view ? await viewColumnIds(supabase, ws, view.columns) : null;

  const page = (offset: number) =>
    supabase.rpc('account_table', { p_ws: ws, p_brief: lens.briefId, p_filter: filter, p_sort: sortAst(sorts), p_columns: columnIds, p_limit: PAGE, p_offset: offset });
  const first = await page(0);
  if (first.error) {
    if (first.error.code === '42501') return text(403, 'Forbidden');
    if (first.error.code === '22023') return text(400, first.error.message);
    console.error(`accounts export: account_table failed for workspace ${ws}: ${first.error.message}`);
    return text(500, 'Export failed');
  }
  const head = first.data as TableData;
  const columns = head.columns;
  const want = Math.min(head.total, MAX_ROWS);

  /** Source URLs for the claims these rows show, 100 claim ids a request. */
  async function sources(rows: TableRow[]): Promise<Map<string, string>> {
    const ids = [...new Set(rows.flatMap((r) => columns.map((c) => r.cells[c.id]?.claim_id).filter((x): x is string => !!x)))];
    const out = new Map<string, string>();
    for (let i = 0; i < ids.length; i += 100) {
      const { data, error } = await supabase!
        .from('claim')
        .select('id, source_record(url)')
        .in('id', ids.slice(i, i + 100));
      if (error) throw new Error(error.message);
      for (const c of (data ?? []) as unknown as { id: string; source_record: { url: string | null } | { url: string | null }[] | null }[]) {
        const sr = Array.isArray(c.source_record) ? c.source_record[0] : c.source_record;
        if (sr?.url) out.set(c.id, sr.url);
      }
    }
    return out;
  }

  const labels = ['Account', 'Country', 'Verdict', 'Score', 'Band', 'Stage', 'Owner', 'Last signal'];
  const header = [...labels, ...columns.flatMap((c) => (evidence ? [c.label, `${c.label} source`] : [c.label]))];
  const toLines = async (rows: TableRow[]) => {
    const src = evidence ? await sources(rows) : new Map<string, string>();
    return rows
      .map((r) =>
        [
          r.name,
          r.country ?? '',
          r.verdict ?? '',
          r.priority != null ? String(Math.round(r.priority)) : '',
          r.band ?? '',
          r.stage,
          r.owner?.name ?? '',
          formatDate(r.last_signal) ?? '',
          ...columns.flatMap((c) => {
            const v = cellText(r, c);
            const id = r.cells[c.id]?.claim_id;
            return evidence ? [v, (id && src.get(id)) || ''] : [v];
          }),
        ]
          .map(csvCell)
          .join(',') + '\r\n',
      )
      .join('');
  };

  const encoder = new TextEncoder();
  let sent = 0;
  let nextRows: TableRow[] | null = head.rows;
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      controller.enqueue(encoder.encode(`﻿${header.map(csvCell).join(',')}\r\n`));
    },
    async pull(controller) {
      try {
        const rows: TableRow[] = nextRows ?? ((await page(sent).then((r) => (r.error ? Promise.reject(new Error(r.error.message)) : r.data))) as TableData).rows;
        nextRows = null;
        const slice = rows.slice(0, want - sent);
        if (slice.length === 0) return controller.close();
        controller.enqueue(encoder.encode(await toLines(slice)));
        sent += slice.length;
        if (sent >= want || rows.length < PAGE) controller.close();
      } catch (e) {
        console.error(`accounts export: stopped after ${sent} rows for workspace ${ws}: ${e instanceof Error ? e.message : String(e)}`);
        controller.error(e);
      }
    },
  });

  return new Response(body, {
    headers: {
      'content-type': 'text/csv; charset=utf-8',
      'content-disposition': `attachment; filename="accounts-${new Date().toISOString().slice(0, 10)}.csv"`,
      'cache-control': 'no-store',
    },
  });
}
