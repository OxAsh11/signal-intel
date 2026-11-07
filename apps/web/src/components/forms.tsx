'use client';

import { useActionState, type ReactNode } from 'react';
import type { ActionState } from '@/app/actions/access';

type Action = (state: ActionState, form: FormData) => Promise<ActionState>;

export const inputClass =
  'h-8 rounded-sm border border-line-strong bg-surface px-2.5 text-[14px] text-ink placeholder:text-ink-faint focus-visible:shadow-[var(--focus-ring)] focus-visible:outline-none';

/** A form bound to a server action, showing its error inline and disabling itself while pending. */
export function ActionForm({
  action,
  children,
  className,
  successText,
}: {
  action: Action;
  children: ReactNode;
  className?: string;
  successText?: string;
}) {
  const [state, formAction, pending] = useActionState(action, {});
  return (
    <form action={formAction} className={className}>
      <fieldset disabled={pending} className="contents">
        {children}
      </fieldset>
      {state.error ? (
        <p role="alert" className="m-0 basis-full text-[13px] text-danger">
          {state.error}
        </p>
      ) : null}
      {state.ok && successText ? <p className="m-0 basis-full text-[13px] text-positive">{successText}</p> : null}
    </form>
  );
}
