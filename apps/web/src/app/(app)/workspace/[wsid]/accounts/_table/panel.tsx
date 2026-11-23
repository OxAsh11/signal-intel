'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { enterCellValue, estimateCellRerun, loadCellDetail, markCellWrong, rerunCell, type CellDetail, type Estimate } from '@/app/actions/cells';
import { formatDate, formatValue } from './cell';
import { OPEN_CELL_EVENT, type OpenCellDetail } from './table';

const field = 'w-full rounded-sm border border-line-strong bg-surface px-3 py-2 text-[15px] text-ink';
const REASON_MAX = 500;
const usd = (n: number) => `$${n.toFixed(2)}`;
const stamp = (v: string | null) => (v ? (formatDate(v) ?? v) : '—');
// Run history shows the time too: "5 Mar 2026, 14:03" (the viewer's local time).
const timeFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const stampTime = (v: string | null) => {
  if (!v) return '—';
  const t = Date.parse(v);
  return Number.isNaN(t) ? v : timeFmt.format(t);
};
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);

type Mode = 'view' | 'edit' | 'wrong' | 'rerun';
type Target = { entityId: string; predicateId: string };

/**
 * The cell panel (05 §3.2): a right-side drawer for one cell, opened by `si:open-cell` from the
 * table. Opening only reads (public.cell_detail). Re-run asks for the estimate and a confirm;
 * Edit and Mark wrong are Editor+ (Viewers see no action buttons; the database refuses them too).
 */
export function CellPanel({ ws, briefId, canEdit, userName }: { ws: string; briefId: string | null; canEdit: boolean; userName: string }) {
  const [target, setTarget] = useState<Target | null>(null);
  const [detail, setDetail] = useState<CellDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>('view');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [queued, setQueued] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [reason, setReason] = useState('');
  const returnFocus = useRef<HTMLElement | null>(null);
  const panelRef = useRef<HTMLElement>(null);
  const seq = useRef(0);

  const load = useCallback(
    async (t: Target) => {
      const mine = ++seq.current;
      const res = await loadCellDetail(ws, t.entityId, t.predicateId);
      if (mine !== seq.current) return;
      if (res.ok) {
        setDetail(res.data);
        setLoadError(null);
      } else setLoadError(res.error);
    },
    [ws],
  );

  useEffect(() => {
    function onOpen(e: Event) {
      const d = (e as CustomEvent<OpenCellDetail>).detail;
      returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      const t = { entityId: d.entityId, predicateId: d.predicateId };
      setTarget(t);
      setDetail(null);
      setLoadError(null);
      setMode('view');
      setError(null);
      setEstimate(null);
      setQueued(null);
      void load(t);
    }
    window.addEventListener(OPEN_CELL_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_CELL_EVENT, onOpen);
  }, [load]);

  useEffect(() => {
    if (target) panelRef.current?.focus();
  }, [target]);

  const close = () => {
    seq.current++;
    setTarget(null);
    returnFocus.current?.focus();
  };

  if (!target) return null;
  // The claim the cell shows. Other active claims of the cell are overridden by it (shown struck
  // through), and Mark wrong acts on it, never on whichever active claim happens to sort first.
  const current = detail?.claim_id ? (detail.claims.find((c) => c.id === detail.claim_id && c.status === 'active') ?? null) : null;
  const type = detail?.column.value_type ?? 'text';
  const options = strings(detail?.column.allowed_values);
  const runnable = detail?.column.fill_method === 'extract' || detail?.column.fill_method === 'formula';

  const go = (m: Mode) => {
    setMode(m);
    setError(null);
  };

  async function startRerun() {
    if (!detail) return;
    go('rerun');
    setEstimate(null);
    const res = await estimateCellRerun(ws, target!.entityId, target!.predicateId, briefId);
    if (res.ok) setEstimate(res.data);
    else setError(res.error);
  }

  async function confirmRerun() {
    setBusy(true);
    const res = await rerunCell(ws, target!.entityId, target!.predicateId, briefId);
    setBusy(false);
    if (!res.ok) return setError(res.error);
    setQueued(res.data.queued);
    go('view');
    await load(target!);
  }

  async function saveEdit() {
    let value: boolean | number | string;
    const raw = draft.trim();
    if (type === 'boolean') {
      if (raw !== 'true' && raw !== 'false') return setError('Choose Yes or No.');
      value = raw === 'true';
    } else if (type === 'number') {
      const n = Number(raw);
      if (raw === '' || !Number.isFinite(n)) return setError('Enter a number.');
      value = n;
    } else {
      if (raw === '') return setError('Enter a value.');
      value = raw;
    }
    setBusy(true);
    const res = await enterCellValue(ws, target!.entityId, target!.predicateId, value);
    setBusy(false);
    if (!res.ok) return setError(res.error);
    go('view');
    await load(target!);
  }

  async function saveWrong() {
    if (!current) return;
    setBusy(true);
    const res = await markCellWrong(ws, current.id, reason);
    setBusy(false);
    if (!res.ok) return setError(res.error);
    setReason('');
    go('view');
    await load(target!);
  }

  const err = error ? (
    <p role="alert" className="m-0 text-[13px] text-danger">
      {error}
    </p>
  ) : null;

  return (
    <div className="fixed inset-0 z-30 flex justify-end bg-black/20" onClick={close} data-cell-panel-backdrop="">
      <aside
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Cell detail"
        data-cell-panel=""
        className="flex h-full w-[min(480px,100%)] flex-col gap-5 overflow-y-auto border-l border-line bg-surface p-5 shadow-lg outline-none"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation();
            close();
          }
        }}
      >
        <header className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="si-h2 m-0 truncate">{detail?.column.label ?? 'Cell'}</h2>
            <p className="m-0 text-[12px] text-ink-muted">{detail ? `${detail.column.value_type} · ${detail.fill_label}` : 'Loading…'}</p>
          </div>
          <button type="button" className="si-btn si-btn-quiet si-btn-sm" onClick={close}>
            Close
          </button>
        </header>

        {loadError ? (
          <div role="alert" className="rounded-md border border-line p-3 text-[13px] text-danger">
            {loadError}{' '}
            <button type="button" className="si-btn si-btn-sm" onClick={() => void load(target)}>
              Retry
            </button>
          </div>
        ) : null}
        {!detail && !loadError ? <p className="m-0 text-[13px] text-ink-muted">Loading…</p> : null}

        {detail ? (
          <>
            <section aria-label="Value" data-panel-value="">
              <p className="si-label m-0 mb-1">Value</p>
              <p className="m-0 text-[20px] font-medium text-ink">{detail.value == null ? '—' : formatValue(detail.value, type)}</p>
              <p className="m-0 mt-1 text-[12px] text-ink-muted">
                {detail.state.replace('_', ' ')}
                {detail.confidence != null ? ` · confidence ${detail.confidence}` : ''} · {detail.fill_label}
              </p>
              {queued != null ? (
                <p role="status" className="m-0 mt-2 text-[13px] text-ink">
                  {queued > 0 ? 'Queued. The cell fills when the run finishes.' : 'Nothing to queue: this cell already has a run open.'}
                </p>
              ) : null}
            </section>

            {canEdit ? (
              <section aria-label="Actions" className="flex flex-col gap-3">
                {mode === 'view' ? (
                  <div className="flex flex-wrap gap-2">
                    {runnable ? (
                      <button type="button" className="si-btn si-btn-sm" onClick={() => void startRerun()}>
                        Re-run
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className="si-btn si-btn-sm"
                      onClick={() => {
                        setDraft(detail.value == null ? '' : String(detail.value));
                        go('edit');
                      }}
                    >
                      Edit
                    </button>
                    {current ? (
                      <button type="button" className="si-btn si-btn-sm" onClick={() => go('wrong')}>
                        Mark wrong
                      </button>
                    ) : null}
                  </div>
                ) : null}

                {mode === 'rerun' ? (
                  <div className="flex flex-col gap-2 rounded-md border border-line p-3" data-panel-form="rerun">
                    <p className="m-0 text-[13px] text-ink" data-panel-estimate="">
                      {estimate
                        ? `${estimate.count} ${estimate.count === 1 ? 'account' : 'accounts'} · est. ${usd(detail.column.fill_method === 'formula' ? 0 : estimate.cost_usd)}`
                        : error
                          ? null
                          : 'Estimating…'}
                    </p>
                    {err}
                    <div className="flex gap-2">
                      <button type="button" className="si-btn si-btn-primary si-btn-sm" disabled={busy || !estimate} onClick={() => void confirmRerun()}>
                        Confirm re-run
                      </button>
                      <button type="button" className="si-btn si-btn-quiet si-btn-sm" onClick={() => go('view')}>
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : null}

                {mode === 'edit' ? (
                  <form
                    className="flex flex-col gap-2 rounded-md border border-line p-3"
                    data-panel-form="edit"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void saveEdit();
                    }}
                  >
                    <label htmlFor="cell-edit" className="si-label">
                      New value
                    </label>
                    {type === 'boolean' ? (
                      <select id="cell-edit" className={field} value={draft} onChange={(e) => setDraft(e.target.value)}>
                        <option value="">Choose…</option>
                        <option value="true">Yes</option>
                        <option value="false">No</option>
                      </select>
                    ) : type === 'enum' ? (
                      <select id="cell-edit" className={field} value={draft} onChange={(e) => setDraft(e.target.value)}>
                        <option value="">Choose…</option>
                        {/* A value no longer allowed stays choosable, so opening Edit never silently changes it. */}
                        {[...options, ...(draft && !options.includes(draft) ? [draft] : [])].map((o) => (
                          <option key={o} value={o}>
                            {o}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        id="cell-edit"
                        className={field}
                        type={type === 'number' ? 'number' : type === 'date' ? 'date' : 'text'}
                        step={type === 'number' ? 'any' : undefined}
                        placeholder={type === 'entity_ref' ? 'Entity id' : undefined}
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        autoFocus
                      />
                    )}
                    <p className="m-0 rounded-sm bg-surface-sunken p-2 text-[12px] text-ink-muted" data-panel-note="evidence">
                      This counts as evidence. A value you enter can change this account&apos;s verdict and score. It&apos;s shown as &quot;Entered by {userName}&quot;, and you can undo it any time with Mark wrong.
                    </p>
                    {type === 'entity_ref' ? (
                      <p className="m-0 text-[12px] text-ink-muted">The id of a company or person in this workspace; it is checked when you save.</p>
                    ) : null}
                    {err}
                    <div className="flex gap-2">
                      <button type="submit" className="si-btn si-btn-primary si-btn-sm" disabled={busy}>
                        Save value
                      </button>
                      <button type="button" className="si-btn si-btn-quiet si-btn-sm" onClick={() => go('view')}>
                        Cancel
                      </button>
                    </div>
                  </form>
                ) : null}

                {mode === 'wrong' ? (
                  <form
                    className="flex flex-col gap-2 rounded-md border border-line p-3"
                    data-panel-form="wrong"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void saveWrong();
                    }}
                  >
                    <label htmlFor="cell-wrong" className="si-label">
                      Why is this value wrong?
                    </label>
                    <textarea
                      id="cell-wrong"
                      className={field}
                      rows={3}
                      maxLength={REASON_MAX}
                      required
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      autoFocus
                    />
                    <p className="m-0 text-[12px] text-ink-muted">
                      {reason.length}/{REASON_MAX}. The value is withdrawn and a Review item is created; it stays withdrawn until you restore it from Review.
                    </p>
                    {err}
                    <div className="flex gap-2">
                      <button type="submit" className="si-btn si-btn-danger si-btn-sm" disabled={busy || reason.trim() === ''}>
                        Confirm mark wrong
                      </button>
                      <button type="button" className="si-btn si-btn-quiet si-btn-sm" onClick={() => go('view')}>
                        Cancel
                      </button>
                    </div>
                  </form>
                ) : null}
              </section>
            ) : null}

            <section aria-label="Evidence" data-panel-evidence="">
              <p className="si-label m-0 mb-2">Evidence</p>
              {detail.inputs.length > 0 ? (
                <ul className="m-0 mb-2 flex list-none flex-col gap-2 p-0" data-panel-inputs="">
                  {detail.inputs.map((i, n) => (
                    <li key={`${i.claim_id ?? 'x'}-${n}`} className="rounded-md border border-line p-2 text-[13px]">
                      <span className="text-ink-muted">{i.column_label}</span>: <span className="font-medium text-ink">{i.value == null ? '—' : formatValue(i.value, 'text')}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {detail.claims.length === 0 && detail.inputs.length === 0 ? <p className="m-0 text-[13px] text-ink-muted">No evidence yet.</p> : null}
              <ul className="m-0 flex list-none flex-col gap-2 p-0">
                {detail.claims.map((c) => {
                  const manual = c.quote?.startsWith('Entered by ') ?? false;
                  // An active claim that is not the cell's is overridden by the cell's value: struck, like a superseded one.
                  const overridden = c.status === 'active' && current != null && c.id !== current.id;
                  const status = overridden ? 'superseded' : c.status;
                  const struck = status !== 'active';
                  return (
                    <li
                      key={c.id}
                      data-claim-status={status}
                      data-claim-current={c.id === current?.id ? '' : undefined}
                      className="rounded-md border border-line p-2 text-[13px]"
                    >
                      <p className={`m-0 font-medium text-ink ${struck ? 'line-through' : ''}`}>{formatValue(c.value, type)}</p>
                      {c.quote ? <p className={`m-0 mt-1 text-ink-muted ${struck ? 'line-through' : ''}`}>&ldquo;{c.quote}&rdquo;</p> : null}
                      {manual && !struck ? <p className="m-0 mt-1 text-[12px] text-ink-muted">Counts as evidence · undo with Mark wrong</p> : null}
                      <p className="m-0 mt-1 text-[12px] text-ink-muted">
                        {c.source_url && /^https?:\/\//.test(c.source_url) ? (
                          <a href={c.source_url} target="_blank" rel="noreferrer" className="text-ink underline">
                            Source
                          </a>
                        ) : null}
                        {c.source_url ? ' · ' : ''}
                        {stamp(c.observed_at)}
                        {c.status === 'superseded' ? ` · superseded ${stamp(c.superseded_at)}` : ''}
                        {overridden ? ` · overridden by the newer value ${formatValue(current.value, type)} (${stamp(current.observed_at)})` : ''}
                        {c.status === 'retracted' ? ' · marked wrong' : ''}
                      </p>
                    </li>
                  );
                })}
              </ul>
            </section>

            <section aria-label="History" data-panel-history="">
              <p className="si-label m-0 mb-2">History</p>
              {detail.runs.length === 0 ? <p className="m-0 text-[13px] text-ink-muted">No runs yet.</p> : null}
              <ul className="m-0 flex list-none flex-col gap-2 p-0">
                {detail.runs.map((r) => (
                  <li key={r.id} className="rounded-md border border-line p-2 text-[13px]">
                    <p className="m-0 text-ink">
                      <span className="font-medium">{r.status.replace('_', ' ')}</span>
                      {r.cost_usd != null ? ` · ${usd(Number(r.cost_usd))}` : ''}
                      {r.brief_version != null ? ` · Brief v${r.brief_version}` : ''}
                    </p>
                    <p className="m-0 text-[12px] text-ink-muted">
                      Started {stampTime(r.started_at ?? r.queued_at)} · finished {stampTime(r.finished_at)}
                    </p>
                    {r.error ? <p className="m-0 mt-1 text-[12px] text-danger">{r.error}</p> : null}
                  </li>
                ))}
              </ul>
            </section>
          </>
        ) : null}
      </aside>
    </div>
  );
}
