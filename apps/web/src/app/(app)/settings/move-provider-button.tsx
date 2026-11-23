'use client';

import { useRouter } from 'next/navigation';
import { moveProvider } from '@/app/actions/providers';

type Props = {
  ws: string;
  order: string[];
  from: number;
  delta: -1 | 1;
  label: string;
  glyph: string;
  disabled?: boolean;
};

/**
 * A form action writes the new order on the server. The streamed Settings panel does not always
 * pick up the action's RSC re-render, so refresh the client router after the write: the list then
 * shows the saved order on the same click, and works the same way after a reload.
 */
export function MoveProviderButton({ ws, order, from, delta, label, glyph, disabled }: Props) {
  const router = useRouter();
  return (
    <form
      action={async (form: FormData) => {
        await moveProvider(form);
        router.refresh();
      }}
    >
      <input type="hidden" name="ws" value={ws} />
      <input type="hidden" name="order" value={JSON.stringify(order)} />
      <input type="hidden" name="from" value={from} />
      <input type="hidden" name="delta" value={delta} />
      <button
        type="submit"
        className="si-btn si-btn-sm"
        aria-label={`Move ${label} ${delta < 0 ? 'up' : 'down'}`}
        disabled={disabled}
      >
        {glyph}
      </button>
    </form>
  );
}
