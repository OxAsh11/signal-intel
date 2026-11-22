'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState, useTransition } from 'react';
import { cx } from '@signal/ui';
import { deleteView, pinView, saveView } from '@/app/actions/views';
import { FilterBuilder, parseItems, type FilterColumn } from './filter-builder';
import { tableHref, type SortAst, type TableQuery } from './url';

export type ViewRow = { id: string | null; preset: string | null; name: string; visibility: string };

const pill = 'rounded-full border px-3 py-1 text-[13px] leading-5 no-underline whitespace-nowrap hover:no-underline';

/**
 * The view bar (T6): the five presets, then the saved views, then Filters (the chip builder),
 * Save view and Pin to sidebar. A Viewer saves personal views and pins; the workspace option is
 * shown to an Editor or above only (the database refuses it otherwise).
 */
export function ViewBar({
  ws,
  q,
  views,
  pinned,
  canWorkspace,
  columns,
  filter,
  sort,
  columnKeys,
  briefId,
}: {
  ws: string;
  q: TableQuery;
  views: ViewRow[];
  /** The refs (preset name or view id) this member pinned. */
  pinned: string[];
  canWorkspace: boolean;
  columns: FilterColumn[];
  /** The filter the table shows now (`f`, else the view's own). */
  filter: Record<string, unknown> | null;
  sort: SortAst;
  /** The predicate columns the table shows now, by key. */
  columnKeys: string[];
  /** The lens Brief (null under All Briefs). */
  briefId: string | null;
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [builder, setBuilder] = useState(q.f !== undefined);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');
  const [visibility, setVisibility] = useState<'personal' | 'workspace'>('personal');
  const [error, setError] = useState<string | null>(null);

  const presets = views.filter((v) => v.preset);
  const saved = views.filter((v) => !v.preset && v.id);
  const refOf = (v: ViewRow) => (v.preset ?? v.id) as string;
  const current = views.find((v) => refOf(v) === q.view) ?? null;
  const isPinned = current ? pinned.includes(refOf(current)) : false;
  const items = parseItems(columns, filter);
  const conditions = items === null ? null : items.reduce((n, i) => n + (i.type === 'cond' ? 1 : i.conds.length), 0);
  const filtered = filter !== null && Object.keys(filter).length > 0;
  const canDelete = current !== null && current.id !== null && (current.visibility === 'personal' || canWorkspace);

  const link = (v: ViewRow) => (
    <Link
      key={refOf(v)}
      href={tableHref(ws, q, { view: refOf(v), f: null, sort: null, page: 1 })}
      aria-current={refOf(v) === q.view && q.f === undefined ? 'true' : undefined}
      title={v.preset ? undefined : v.visibility === 'personal' ? 'Personal view' : 'Workspace view'}
      className={cx(pill, refOf(v) === q.view ? 'border-transparent bg-signal-soft font-medium text-signal-strong' : 'border-line text-ink hover:bg-surface-sunken')}
    >
      {v.name}
    </Link>
  );

  function onSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    start(async () => {
      const res = await saveView(ws, {
        name,
        visibility,
        brief: briefId,
        filter: filtered ? filter : null,
        columns: columnKeys.map((ref) => ({ kind: 'predicate' as const, ref })),
        sort,
        layout: q.layout === 'board' ? 'board' : q.layout === 'country' ? 'map' : 'table',
      });
      if (!res.ok) return setError(res.error);
      setSaving(false);
      setName('');
      router.push(tableHref(ws, q, { view: res.data.id, f: null, sort: null, page: 1 }));
    });
  }

  function onPin(next: boolean) {
    if (!current) return;
    setError(null);
    start(async () => {
      const res = await pinView(ws, refOf(current), next);
      if (!res.ok) setError(res.error);
    });
  }

  function onDelete() {
    if (!current?.id || !window.confirm(`Delete the view "${current.name}"?`)) return;
    setError(null);
    start(async () => {
      const res = await deleteView(current.id as string);
      if (!res.ok) return setError(res.error);
      router.push(tableHref(ws, q, { view: null, f: null, sort: null, page: 1 }));
    });
  }

  return (
    <div className="flex flex-col gap-3" data-view-bar="">
      <nav aria-label="Views" className="flex flex-wrap items-center gap-1.5">
        <Link
          href={tableHref(ws, q, { view: null, f: null, sort: null, page: 1 })}
          className={cx(pill, !q.view && !filtered ? 'border-transparent bg-signal-soft font-medium text-signal-strong' : 'border-line text-ink hover:bg-surface-sunken')}
        >
          All accounts
        </Link>
        {presets.map(link)}
        {saved.length ? <span aria-hidden="true" className="mx-1 h-4 w-px bg-line" /> : null}
        {saved.map(link)}
      </nav>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="si-btn si-btn-sm" aria-expanded={builder} onClick={() => setBuilder((b) => !b)}>
          Filters{conditions ? ` (${conditions})` : conditions === null ? ' (custom)' : ''}
        </button>
        <button type="button" className="si-btn si-btn-sm" aria-expanded={saving} onClick={() => { setSaving((s) => !s); setError(null); }}>
          Save view
        </button>
        {current ? (
          <button
            type="button"
            className="si-btn si-btn-sm"
            disabled={pending || q.f !== undefined}
            title={q.f !== undefined ? 'Save this filter as a view to pin it' : undefined}
            onClick={() => onPin(!isPinned)}
          >
            {isPinned ? 'Unpin from sidebar' : 'Pin to sidebar'}
          </button>
        ) : null}
        {canDelete ? (
          <button type="button" className="si-btn si-btn-quiet si-btn-sm" disabled={pending} onClick={onDelete}>
            Delete view
          </button>
        ) : null}
        {error ? <span role="alert" className="text-[13px] text-danger">{error}</span> : null}
      </div>
      {saving ? (
        <form onSubmit={onSave} className="flex flex-wrap items-end gap-3 rounded-md border border-line bg-surface p-3" aria-label="Save view" data-save-view="">
          <div className="flex flex-col gap-1">
            <label htmlFor="save-view-name" className="si-label">View name</label>
            <input
              id="save-view-name"
              className="w-60 rounded-sm border border-line-strong bg-surface px-3 py-1.5 text-[14px] text-ink"
              value={name}
              maxLength={120}
              autoFocus
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <fieldset className="m-0 flex flex-col gap-1 border-0 p-0">
            <legend className="si-label p-0">Who sees it</legend>
            <span className="flex items-center gap-3 text-[13px] text-ink">
              <label className="flex items-center gap-1.5">
                <input type="radio" name="view-visibility" className="accent-[var(--ink)]" checked={visibility === 'personal'} onChange={() => setVisibility('personal')} />
                Personal
              </label>
              {canWorkspace ? (
                <label className="flex items-center gap-1.5">
                  <input type="radio" name="view-visibility" className="accent-[var(--ink)]" checked={visibility === 'workspace'} onChange={() => setVisibility('workspace')} />
                  Workspace
                </label>
              ) : null}
            </span>
          </fieldset>
          <button type="submit" className="si-btn si-btn-primary si-btn-sm" disabled={pending || name.trim().length === 0}>
            Save
          </button>
        </form>
      ) : null}
      {builder ? <FilterBuilder key={JSON.stringify(filter)} ws={ws} q={q} columns={columns} filter={filter} /> : null}
    </div>
  );
}
