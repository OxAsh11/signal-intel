'use client';

import { useEffect, useRef, useState } from 'react';
import { estimateRun, requestRun, type RunEstimate, type RunTarget } from '@/app/actions/columns';

export type RunJob = RunTarget & { label: string };
type Outcome = { queued: number } | { error: string; firstN?: number };

const nf = new Intl.NumberFormat('en');
export const usd = (n: number) => `$${n.toFixed(2)}`;
/** The line every run shows before it is confirmed: `N accounts · est. $X`. */
export const estimateText = (count: number, cost: number) => `${nf.format(count)} ${count === 1 ? 'account' : 'accounts'} · est. ${usd(cost)}`;

/**
 * Queues a run and keeps its outcome. A budget refusal (55000) shows the server's message and
 * offers "Run the first N", which asks again with p_limit = N.
 */
export function useRun(ws: string) {
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(job: RunTarget, limit?: number) {
    setBusy(true);
    const res = await requestRun(ws, job, limit);
    setBusy(false);
    const o: Outcome = res.ok ? { queued: res.data.queued } : { error: res.error, firstN: res.firstN };
    setOutcome(o);
    return o;
  }
  return { outcome, busy, run, reset: () => setOutcome(null) };
}

/** What a run request came to: queued, or why not (with "Run the first N" for a budget refusal). */
export function RunOutcome({ outcome, busy, onFirstN }: { outcome: Outcome | null; busy: boolean; onFirstN: (n: number) => void }) {
  if (!outcome) return null;
  if ('queued' in outcome) {
    return (
      <p role="status" className="m-0 text-[13px] text-ink" data-run-queued={outcome.queued}>
        {outcome.queued > 0
          ? `Queued ${nf.format(outcome.queued)} ${outcome.queued === 1 ? 'run' : 'runs'}. Cells fill as the runs finish.`
          : 'Nothing to queue: those cells already have a run open, or none matched.'}
      </p>
    );
  }
  return (
    <div role="alert" className="flex flex-col gap-2 rounded-md border border-danger/40 bg-danger/5 p-3 text-[13px] text-danger" data-run-refusal="">
      <p className="m-0">{outcome.error}</p>
      {outcome.firstN ? (
        <div>
          <button type="button" className="si-btn si-btn-sm" disabled={busy} onClick={() => onFirstN(outcome.firstN!)}>
            Run the first {nf.format(outcome.firstN)}
          </button>
        </div>
      ) : null}
    </div>
  );
}

/** One job of the confirm: its estimate, then its outcome. */
function JobLine({ ws, job, estimate, error, confirmed }: {
  ws: string;
  job: RunJob;
  estimate: RunEstimate | null;
  error: string | null;
  /** Confirm was pressed and this job has cells to run. */
  confirmed: boolean;
}) {
  const { outcome, busy, run } = useRun(ws);
  const started = useRef(false);
  useEffect(() => {
    if (!confirmed || started.current) return;
    started.current = true;
    void run(job);
  }, [confirmed, job, run]);
  return (
    <li className="flex flex-col gap-2 rounded-md border border-line p-3" data-run-job={job.label}>
      <p className="m-0 text-[13px] text-ink">
        <span className="font-medium">{job.label}</span>
        {': '}
        <span data-run-estimate="">{estimate ? estimateText(estimate.count, estimate.cost_usd) : error ? '' : 'Estimating…'}</span>
      </p>
      {error ? <p role="alert" className="m-0 text-[13px] text-danger">{error}</p> : null}
      {busy && !outcome ? <p className="m-0 text-[13px] text-ink-muted">Queuing…</p> : null}
      <RunOutcome outcome={outcome} busy={busy} onFirstN={(n) => void run(job, n)} />
    </li>
  );
}

/**
 * The run confirm (T4, T9): every run shows `N accounts · est. $X` (public.estimate_column_run)
 * before anything is queued; Confirm queues each job (public.request_column_run). Nothing runs
 * until Confirm is pressed.
 */
export function RunConfirm({ ws, title, jobs, onClose }: { ws: string; title: string; jobs: RunJob[]; onClose: () => void }) {
  const [estimates, setEstimates] = useState<(RunEstimate | null)[]>(() => jobs.map(() => null));
  const [errors, setErrors] = useState<(string | null)[]>(() => jobs.map(() => null));
  const [confirmed, setConfirmed] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    let live = true;
    void Promise.all(jobs.map((j) => estimateRun(ws, j))).then((all) => {
      if (!live) return;
      setEstimates(all.map((r) => (r.ok ? r.data : null)));
      setErrors(all.map((r) => (r.ok ? null : r.error)));
    });
    return () => {
      live = false;
    };
  }, [ws, jobs]);

  const close = () => {
    onClose();
    returnFocus.current?.focus();
  };
  const ready = estimates.every((e, i) => e !== null || errors[i] !== null);
  const runnable = estimates.some((e) => e && e.count > 0);
  const total = estimates.reduce((s, e) => s + (e?.cost_usd ?? 0), 0);
  const runs = estimates.reduce((s, e) => s + (e?.count ?? 0), 0);

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/20 p-4" onClick={close}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Confirm run"
        tabIndex={-1}
        data-run-confirm=""
        className="flex max-h-[90vh] w-[min(520px,100%)] flex-col gap-4 overflow-y-auto rounded-lg border border-line bg-surface p-5 shadow-lg outline-none"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation();
            close();
          }
        }}
      >
        <h2 className="si-h2 m-0">{title}</h2>
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {jobs.map((j, i) => (
            <JobLine
              key={`${j.predicate}-${i}`}
              ws={ws}
              job={j}
              estimate={estimates[i]}
              error={errors[i]}
              confirmed={confirmed && (estimates[i]?.count ?? 0) > 0}
            />
          ))}
        </ul>
        {jobs.length > 1 && ready ? (
          <p className="m-0 text-[13px] text-ink-muted" data-run-total="">
            {nf.format(runs)} {runs === 1 ? 'run' : 'runs'} in all · est. {usd(total)}
          </p>
        ) : null}
        {ready && !runnable && !confirmed ? <p className="m-0 text-[13px] text-ink-muted">Nothing to run: no cell matches.</p> : null}
        <div className="flex gap-2">
          {!confirmed ? (
            <button type="button" className="si-btn si-btn-primary si-btn-sm" disabled={!ready || !runnable} onClick={() => setConfirmed(true)}>
              Confirm run
            </button>
          ) : null}
          <button type="button" className="si-btn si-btn-quiet si-btn-sm" onClick={close}>
            {confirmed ? 'Done' : 'Cancel'}
          </button>
        </div>
      </div>
    </div>
  );
}
