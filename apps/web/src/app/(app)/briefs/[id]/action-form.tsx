'use client';
import { useActionState } from 'react';
import { Button } from '@signal/ui';
import type { ActionState } from '@/app/actions/access';

type Props = {
  action: (prev: ActionState, form: FormData) => Promise<ActionState>;
  fields: Record<string, string>;
  label: string;
  variant?: 'primary' | 'danger' | 'quiet';
  disabled?: boolean;
};

/** One button that posts hidden fields to a server action and shows its error underneath. */
export function ActionForm({ action, fields, label, variant = 'primary', disabled }: Props) {
  const [state, formAction, isPending] = useActionState(action, {});
  return (
    <form action={formAction} className="inline-flex flex-col gap-1">
      <fieldset disabled={isPending || disabled} className="contents">
        {Object.entries(fields).map(([name, value]) => (
          <input key={name} type="hidden" name={name} value={value} />
        ))}
        <Button type="submit" variant={variant} size="sm">
          {label}
        </Button>
      </fieldset>
      {state?.error ? <p className="m-0 text-xs text-danger">{state.error}</p> : null}
    </form>
  );
}
