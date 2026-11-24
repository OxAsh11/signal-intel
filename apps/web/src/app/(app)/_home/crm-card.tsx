import Link from 'next/link';
import { Card } from '@signal/ui';
import { CardError } from './card-error';
import { type HomeCardProps, fmt, loadCard, plural } from './shared';

type Crm = {
  stages: { stage: string; count: number; href: string }[];
  tasks_due_today: { count: number; href: string };
};

/**
 * Accounts by stage (every stage, counts as links to the pipeline) and tasks due today. The bars
 * use the card's grass family tokens (--f02 track, --f09 bar), never a raw colour.
 */
export async function CrmCard(props: HomeCardProps) {
  const c = await loadCard<Crm>('home_crm', props);
  if (!c) return <CardError id="home-crm" title="CRM pipeline" retry={props.retry} />;
  const max = Math.max(1, ...c.stages.map((s) => Number(s.count)));
  const tasks = c.tasks_due_today;
  return (
    <Card aria-labelledby="home-crm">
      <div className="flex items-center justify-between gap-2">
        <h2 id="home-crm" className="si-h2">CRM pipeline</h2>
        <Link href="/crm/pipeline" className="text-[13px]">Open →</Link>
      </div>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {c.stages.map((s) => (
          <li key={s.stage} data-stage={s.stage} className="grid grid-cols-[96px_minmax(0,1fr)_32px] items-center gap-2 text-[13px]">
            <span className="truncate text-ink-body">{s.stage}</span>
            <span aria-hidden="true" className="h-1.5 rounded-[2px] bg-surface-sunken">
              <span className="block h-1.5 rounded-[2px] bg-signal" style={{ width: `${(Number(s.count) / max) * 100}%` }} />
            </span>
            <Link href={s.href} className="text-right font-mono text-ink" aria-label={`${s.stage}: ${fmt(s.count)}`}>
              {fmt(s.count)}
            </Link>
          </li>
        ))}
      </ul>
      <Link href={tasks.href} className="self-start text-[13px] text-ink" data-tasks="">
        <b className="font-mono font-medium">{fmt(tasks.count)}</b> {plural(tasks.count, 'task', 'tasks')} due today
      </Link>
    </Card>
  );
}
