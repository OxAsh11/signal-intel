'use client';

import { useEffect, useRef, useState } from 'react';
import { estimateFindPeople, requestFindPeople, type FindEstimate } from '@/app/actions/people';

const LIMIT = 5;

/**
 * Find people on the account's People tab. The estimate is shown before anything is queued;
 * Confirm sends one locked job. A budget refusal offers "Find the first N" instead of failing.
 */
export function FindPeople({ ws, account }: { ws: string; account: string }) {
  const [open, setOpen] = useState(false);
  const [estimate, setEstimate] = useState<FindEstimate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    let live = true;
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    void estimateFindPeople(ws, account, LIMIT).then((res) => {
      if (!live) return;
      if (res.ok) setEstimate(res.data);
      else setError(res.error);
    });
    return () => {
      live = false;
    };
  }, [open, ws, account]);

  const close = () => {
    setOpen(false);
    setEstimate(null);
    setError(null);
    setStatus(null);
    setDone(false);
    returnFocus.current?.focus();
  };

  const send = async (limit: number) => {
    setBusy(true);
    setError(null);
    const res = await requestFindPeople(ws, account, limit);
    setBusy(false);
    if (res.ok) {
      setStatus(`Queued a find for ${limit} ${limit === 1 ? 'person' : 'people'}. They appear as the run finishes.`);
      setDone(true);
    } else if (res.firstN && res.firstN > 0) {
      await send(res.firstN);
    } else {
      setError(res.error);
    }
  };

  return (
    <>
      <button type="button" className="si-btn si-btn-sm" data-find-people="" onClick={() => setOpen(true)}>
        Find people
      </button>
      {open ? (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/20 p-4" onClick={close}>
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-label="Find people"
            tabIndex={-1}
            data-find-dialog=""
            className="flex max-h-[90vh] w-[min(480px,100%)] flex-col gap-3 rounded-lg border border-line bg-surface p-5 shadow-lg outline-none"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.stopPropagation();
                close();
              }
            }}
          >
            <h2 className="si-h2 m-0">Find people</h2>
            <p className="m-0 text-[14px] text-ink-body">Look for people who work at this account.</p>
            {estimate ? (
              <p className="m-0 text-[14px] font-medium text-ink" data-find-estimate="">
                {estimate.count} people · est. ${estimate.cost_usd.toFixed(2)}
              </p>
            ) : error ? null : (
              <p className="m-0 text-[13px] text-ink-muted">Estimating…</p>
            )}
            {error ? (
              <p role="alert" className="m-0 text-[13px] text-danger">
                {error}
              </p>
            ) : null}
            {status ? (
              <p role="status" className="m-0 text-[13px] text-ink">
                {status}
              </p>
            ) : null}
            <div className="flex gap-2">
              {!done ? (
                <button
                  type="button"
                  className="si-btn si-btn-primary si-btn-sm"
                  disabled={!estimate || busy}
                  onClick={() => void send(LIMIT)}
                >
                  Confirm
                </button>
              ) : null}
              <button type="button" className="si-btn si-btn-quiet si-btn-sm" onClick={close}>
                {done ? 'Done' : 'Cancel'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
