'use client';

import { useEffect, useRef, useState } from 'react';
import { loadPersonEmail, revealPersonEmail } from '@/app/actions/people';
import { OPEN_PERSON_EVENT, type OpenPersonDetail } from './people-table';

/**
 * The person details dialog. The mask arrives with the click (so even a Viewer sees it); the email
 * itself only comes back from the worker after `person_email` has checked the role and contact id.
 */
export function PersonPanel({ ws }: { ws: string }) {
  const [person, setPerson] = useState<OpenPersonDetail | null>(null);
  const [masked, setMasked] = useState<string | null>(null);
  const [contact, setContact] = useState<string | null>(null);
  const [canReveal, setCanReveal] = useState(false);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<OpenPersonDetail>).detail;
      returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setPerson(detail);
      setMasked(detail.masked);
      setContact(null);
      setCanReveal(false);
      setRevealed(null);
      setError(null);
      void loadPersonEmail(ws, detail.personId).then((res) => {
        if (!res.ok) return; // a Viewer cannot reveal: keep the mask from the row
        setMasked(res.data.masked);
        setContact(res.data.contact_point_id || null);
        setCanReveal(res.data.reveal === 'worker' && !!res.data.contact_point_id);
      });
    };
    window.addEventListener(OPEN_PERSON_EVENT, handler);
    return () => window.removeEventListener(OPEN_PERSON_EVENT, handler);
  }, [ws]);

  useEffect(() => {
    if (person) dialogRef.current?.focus();
  }, [person]);

  if (!person) return null;
  const close = () => {
    setPerson(null);
    returnFocus.current?.focus();
  };
  const reveal = async () => {
    if (!contact) return;
    setBusy(true);
    setError(null);
    const res = await revealPersonEmail(ws, person.personId, contact);
    setBusy(false);
    if (res.ok) setRevealed(res.data.email);
    else setError(res.error);
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/20 p-4" onClick={close}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Person details"
        tabIndex={-1}
        data-person-panel=""
        className="flex max-h-[90vh] w-[min(460px,100%)] flex-col gap-3 rounded-lg border border-line bg-surface p-5 shadow-lg outline-none"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation();
            close();
          }
        }}
      >
        <h2 className="si-h2 m-0">{person.name}</h2>
        <div className="flex flex-col gap-1">
          <span className="si-label">Work email</span>
          <span className="text-[14px] text-ink" data-person-mask="">
            {masked ?? '—'}
          </span>
        </div>
        {canReveal && contact ? (
          revealed ? (
            <p className="m-0 break-all text-[14px] font-medium text-ink" data-person-email="">
              {revealed}
            </p>
          ) : (
            <button type="button" className="si-btn si-btn-sm self-start" disabled={busy} onClick={() => void reveal()}>
              {busy ? 'Revealing…' : 'Reveal email'}
            </button>
          )
        ) : null}
        {error ? (
          <p role="alert" className="m-0 text-[13px] text-danger">
            {error}
          </p>
        ) : null}
        <button type="button" className="si-btn si-btn-quiet si-btn-sm self-start" onClick={close}>
          Close
        </button>
      </div>
    </div>
  );
}
