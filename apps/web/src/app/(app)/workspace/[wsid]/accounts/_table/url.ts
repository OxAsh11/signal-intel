/**
 * The table's URL is its state:
 *   ?brief=<id|all>&view=<preset|saved uuid>&f=<base64url filter AST>&sort=<key:dir,…>
 *    &layout=<table|board|country>&group=<stage|verdict>&page=<n>
 * Pure helpers (no server-only imports) so the client table can build links too.
 */

export const PAGE_SIZE = 50;
export const SYSTEM_SORTS = ['name', 'verdict', 'priority', 'created_at'] as const;
const MAX_F_LENGTH = 16_384;
const MAX_SORTS = 5;

export type SortKey = { key: string; dir: 'asc' | 'desc' };
export type SortAst = ({ system: string; dir: 'asc' | 'desc' } | { column: string; dir: 'asc' | 'desc' })[];

export type TableQuery = {
  brief?: string;
  view?: string;
  /** The raw `f` value; absent when the URL has none. */
  f?: string;
  sort?: string;
  layout?: string;
  group?: string;
  page: number;
};

type Params = Record<string, string | string[] | undefined>;
const first = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

export function readQuery(sp: Params): TableQuery {
  const page = Number.parseInt(first(sp.page) ?? '1', 10);
  const get = (k: string) => first(sp[k]) || undefined;
  return {
    brief: get('brief'),
    view: get('view'),
    f: get('f'),
    sort: get('sort'),
    layout: get('layout'),
    group: get('group'),
    page: Number.isFinite(page) && page >= 1 && page <= 100_000 ? page : 1,
  };
}

/** base64url (RFC 4648 §5, no padding) of the UTF-8 JSON. */
export function encodeFilter(ast: unknown): string {
  const bytes = new TextEncoder().encode(JSON.stringify(ast));
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** The filter AST in `f`, or ok: false for anything that is not base64url JSON object (the server still compiles it). */
export function decodeFilter(f: string): { ok: true; ast: Record<string, unknown> } | { ok: false } {
  if (f.length > MAX_F_LENGTH || !/^[A-Za-z0-9_-]*$/.test(f)) return { ok: false };
  try {
    const std = f.replace(/-/g, '+').replace(/_/g, '/');
    const bin = atob(std + '='.repeat((4 - (std.length % 4)) % 4));
    const text = new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
    const ast: unknown = JSON.parse(text);
    if (ast === null || typeof ast !== 'object' || Array.isArray(ast)) return { ok: false };
    return { ok: true, ast: ast as Record<string, unknown> };
  } catch {
    return { ok: false };
  }
}

/** `name:asc,priority:desc` -> keys; null when malformed. */
export function parseSort(s: string | undefined): SortKey[] | null {
  if (!s) return [];
  const parts = s.split(',');
  if (parts.length > MAX_SORTS) return null;
  const out: SortKey[] = [];
  for (const p of parts) {
    const m = /^([A-Za-z0-9_.-]{1,64})(?::(asc|desc))?$/.exec(p);
    if (!m) return null;
    out.push({ key: m[1], dir: (m[2] as 'asc' | 'desc' | undefined) ?? 'asc' });
  }
  return out;
}

export function sortAst(keys: SortKey[]): SortAst {
  return keys.map((k) =>
    (SYSTEM_SORTS as readonly string[]).includes(k.key) ? { system: k.key, dir: k.dir } : { column: k.key, dir: k.dir },
  );
}

/** A view's stored sort back to keys (for the header arrows). */
export function sortKeysFromAst(ast: unknown): SortKey[] {
  if (!Array.isArray(ast)) return [];
  const out: SortKey[] = [];
  for (const x of ast) {
    if (x === null || typeof x !== 'object') continue;
    const o = x as { system?: unknown; column?: unknown; dir?: unknown };
    const key = typeof o.system === 'string' ? o.system : typeof o.column === 'string' ? o.column : null;
    if (key) out.push({ key, dir: o.dir === 'desc' ? 'desc' : 'asc' });
  }
  return out;
}

export const formatSort = (keys: SortKey[]) => keys.map((k) => `${k.key}:${k.dir}`).join(',');

/** This page's URL with `patch` applied (undefined or null removes a parameter). */
export function tableHref(ws: string, q: TableQuery, patch: Partial<Record<keyof TableQuery, string | number | null | undefined>> = {}): string {
  const next: Record<string, string | number | null | undefined> = { ...q, ...patch };
  const qs = new URLSearchParams();
  for (const k of ['brief', 'view', 'f', 'sort', 'layout', 'group'] as const) {
    const v = next[k];
    if (v != null && v !== '') qs.set(k, String(v));
  }
  const page = Number(next.page ?? 1);
  if (page > 1) qs.set('page', String(page));
  return `/workspace/${ws}/accounts${qs.size ? `?${qs.toString()}` : ''}`;
}

/** The next sort when a header is clicked: asc, then desc, then off. Only the clicked key sorts. */
export function nextSort(current: SortKey[], key: string): SortKey[] {
  const c = current.find((k) => k.key === key);
  if (!c) return [{ key, dir: 'asc' }];
  return c.dir === 'asc' ? [{ key, dir: 'desc' }] : [];
}
