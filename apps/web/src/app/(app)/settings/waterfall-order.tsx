'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { moveProvider } from '@/app/actions/providers';

type Props = {
  ws: string;
  order: string[];
  canEdit: boolean;
  labels: Record<string, string>;
};

/**
 * Waterfall order with an optimistic local list. The Server Action persists the swap; the local
 * state shows it immediately, so a slow or aborted RSC response cannot leave the list looking
 * unchanged. A reload still reads the saved order from the server.
 */
export function WaterfallOrder({ ws, order, canEdit, labels }: Props) {
  const router = useRouter();
  const [current, setCurrent] = useState(order);

  const submit = async (form: FormData) => {
    const from = Number(form.get('from'));
    const delta = Number(form.get('delta'));
    const to = from + delta;
    if (to < 0 || to >= current.length) return;
    const next = [...current];
    [next[from], next[to]] = [next[to]!, next[from]!];
    setCurrent(next);
    try {
      await moveProvider(form);
      router.refresh();
    } catch {
      // The write failed: put the list back to the order the server last rendered.
      setCurrent(order);
    }
  };

  return (
    <div className="flex flex-col gap-2" data-waterfall-order="">
      <h3 className="si-h2">Waterfall order</h3>
      <p className="m-0 text-[13px] text-ink-muted">
        {canEdit ? 'Admins can move a provider up or down. Everyone else sees the order read-only.' : 'The order is read-only for your role.'}
      </p>
      <ol className="m-0 flex flex-col gap-1 p-0">
        {current.map((key, i) => (
          <li key={key} className="flex items-center justify-between gap-2 rounded-md border border-line px-3 py-2">
            <span className="font-semibold" data-order-key={key}>{labels[key] ?? key}</span>
            {canEdit ? (
              <span className="flex items-center gap-1">
                <form action={submit}>
                  <input type="hidden" name="ws" value={ws} />
                  <input type="hidden" name="order" value={JSON.stringify(current)} />
                  <input type="hidden" name="from" value={i} />
                  <input type="hidden" name="delta" value={-1} />
                  <button type="submit" className="si-btn si-btn-sm" aria-label={`Move ${labels[key] ?? key} up`} disabled={i === 0}>↑</button>
                </form>
                <form action={submit}>
                  <input type="hidden" name="ws" value={ws} />
                  <input type="hidden" name="order" value={JSON.stringify(current)} />
                  <input type="hidden" name="from" value={i} />
                  <input type="hidden" name="delta" value={1} />
                  <button type="submit" className="si-btn si-btn-sm" aria-label={`Move ${labels[key] ?? key} down`} disabled={i === current.length - 1}>↓</button>
                </form>
              </span>
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  );
}
