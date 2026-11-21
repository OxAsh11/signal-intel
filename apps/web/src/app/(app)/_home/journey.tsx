import Link from 'next/link';
import { cx } from '@signal/ui';
import { CardError } from './card-error';
import { type HomeCardProps, loadCard } from './shared';

type Journey = { steps: { key: string; done: boolean; href: string }[]; complete: boolean };

const LABEL: Record<string, string> = {
  brief: 'Brief written',
  sources: 'Sources running',
  first_accounts: 'First accounts',
  first_confirmed: 'First Confirmed',
  contacts: 'Contacts found',
  first_email: 'First email sent',
};

const STATE = {
  done: { glyph: '✓', cls: 'bg-positive-soft text-positive' },
  now: { glyph: '◐', cls: 'bg-marker-soft text-caution font-medium' },
  todo: { glyph: '○', cls: 'bg-surface-sunken text-ink-muted' },
} as const;

/**
 * The six steps from a Brief to the first email, each linking to its page. The first step not
 * done is the current one (mustard). Hidden once every step but the first email is done.
 */
export async function Journey(props: HomeCardProps) {
  const j = await loadCard<Journey>('home_journey', props);
  if (!j) return <CardError id="home-journey" title="Getting started" retry={props.retry} />;
  if (j.complete) return null;
  const done = j.steps.filter((s) => s.done).length;
  const current = j.steps.find((s) => !s.done)?.key;
  return (
    <section aria-labelledby="home-journey" className="flex flex-col gap-2">
      <h2 id="home-journey" className="si-label m-0">
        Getting started · {done} of {j.steps.length} done
      </h2>
      <ol className="m-0 flex list-none flex-wrap gap-2 p-0">
        {j.steps.map((s) => {
          const state = s.done ? 'done' : s.key === current ? 'now' : 'todo';
          return (
            <li key={s.key} className="flex min-w-0 flex-[1_1_150px]">
              <Link
                href={s.href}
                data-step={s.key}
                data-state={state}
                className={cx(
                  'flex w-full min-w-0 items-start gap-2 rounded-[6px] px-3 py-2 text-[13px] leading-[18px] no-underline hover:no-underline',
                  STATE[state].cls,
                )}
              >
                <span aria-hidden="true">{STATE[state].glyph}</span>
                <span className="flex min-w-0 flex-col">
                  <span>
                    {LABEL[s.key] ?? s.key}
                    {s.done ? <span className="sr-only"> (done)</span> : null}
                  </span>
                  {s.key === 'first_email' ? <span className="text-[12px] font-normal">Comes with the Email engine</span> : null}
                </span>
              </Link>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
