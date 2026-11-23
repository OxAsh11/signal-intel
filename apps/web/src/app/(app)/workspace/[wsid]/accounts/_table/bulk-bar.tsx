'use client';

import { useState } from 'react';
import { formatDate } from './cell';
import { cellText, csvCell } from './csv';
import type { RunJob } from './run-confirm';
import type { Column, TableRow } from './types';

const SOON: { label: string; why: string }[] = [
  { label: 'Add to sequence', why: 'Arrives with the Email engine' },
  { label: 'Find people', why: 'Arrives in P6' },
  { label: 'Merge', why: 'Arrives in P6' },
];

/** The selected rows as CSV: the columns as shown, values formatted as in the cells. */
function exportSelected(rows: TableRow[], columns: Column[]) {
  const head = ['Account', 'Country', 'Verdict', 'Score', 'Band', 'Stage', 'Owner', 'Last signal', ...columns.map((c) => c.label)];
  const lines = rows.map((r) =>
    [
      r.name,
      r.country ?? '',
      r.verdict ?? '',
      r.priority != null ? String(Math.round(r.priority)) : '',
      r.band ?? '',
      r.stage,
      r.owner?.name ?? '',
      formatDate(r.last_signal) ?? '',
      ...columns.map((c) => cellText(r, c)),
    ].map(csvCell).join(','),
  );
  const blob = new Blob([[head.map(csvCell).join(','), ...lines].join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `accounts-selected-${rows.length}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/**
 * The bulk bar (shown while rows are selected): Run columns… (Editor+, mode selected), Export
 * selected (Viewer+), and the actions that arrive later, shown disabled with when.
 */
export function BulkBar({
  rows,
  columns,
  canEdit,
  briefId,
  onRun,
  onClear,
}: {
  rows: TableRow[];
  columns: Column[];
  canEdit: boolean;
  briefId: string | null;
  onRun: (title: string, jobs: RunJob[]) => void;
  onClear: () => void;
}) {
  const [picking, setPicking] = useState(false);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set());
  const runnable = columns.filter((c) => c.fill_method === 'extract' || c.fill_method === 'formula');
  const n = rows.length;

  function review() {
    const ids = rows.map((r) => r.entity_id);
    const jobs = runnable
      .filter((c) => chosen.has(c.id))
      .map<RunJob>((c) => ({ predicate: c.id, label: c.label, mode: 'selected', brief: briefId, filter: null, entityIds: ids }));
    if (!jobs.length) return;
    setPicking(false);
    setChosen(new Set());
    onRun(`Run ${jobs.length === 1 ? jobs[0].label : `${jobs.length} columns`} on ${n} selected`, jobs);
  }

  return (
    <div className="mb-2 flex flex-col gap-2 rounded-md border border-line bg-surface-sunken px-4 py-2 text-[13px]" role="region" aria-label="Selected accounts">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 font-medium text-ink">{n} selected</span>
        {canEdit && runnable.length > 0 ? (
          <button type="button" className="si-btn si-btn-sm" aria-expanded={picking} onClick={() => setPicking((p) => !p)}>
            Run columns…
          </button>
        ) : null}
        <button type="button" className="si-btn si-btn-sm" onClick={() => exportSelected(rows, columns)}>
          Export selected
        </button>
        {canEdit ? (
          <>
            <button type="button" className="si-btn si-btn-sm" disabled title="Arrives with saved segments">
              Label
            </button>
            {SOON.map((s) => (
              <button key={s.label} type="button" className="si-btn si-btn-sm" disabled title={s.why}>
                {s.label}
                <span className="sr-only"> ({s.why})</span>
              </button>
            ))}
          </>
        ) : null}
        <button type="button" className="si-btn si-btn-quiet si-btn-sm" onClick={onClear}>
          Clear selection
        </button>
      </div>
      {picking ? (
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0" data-run-columns="">
          <legend className="si-label mb-1 p-0">Columns to run on the {n} selected</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {runnable.map((c) => (
              <label key={c.id} className="inline-flex items-center gap-1.5 text-ink">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-[var(--ink)]"
                  checked={chosen.has(c.id)}
                  onChange={() =>
                    setChosen((prev) => {
                      const next = new Set(prev);
                      if (!next.delete(c.id)) next.add(c.id);
                      return next;
                    })
                  }
                />
                {c.label}
              </label>
            ))}
          </div>
          <div>
            <button type="button" className="si-btn si-btn-primary si-btn-sm" disabled={chosen.size === 0} onClick={review}>
              Review run
            </button>
          </div>
        </fieldset>
      ) : null}
    </div>
  );
}
