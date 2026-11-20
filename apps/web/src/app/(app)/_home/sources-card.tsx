import Link from 'next/link';
import { Card, HealthIndicator } from '@signal/ui';
import { CardError } from './card-error';
import { type HomeCardProps, fmt, loadCard, plural } from './shared';

type Radar = { id: string; name: string; type: string; health: 'incident' | 'silent' | 'ok' | 'paused'; records_7d: number; href: string };
type Sources = { radars: Radar[]; href: string };

/** Health per radar in the lens, failures first (the RPC orders them), with records in the last 7 days. */
export async function SourcesCard(props: HomeCardProps) {
  const s = await loadCard<Sources>('home_sources', props);
  if (!s) return <CardError id="home-sources" title="Sources" retry={props.retry} />;
  return (
    <Card aria-labelledby="home-sources">
      <div className="flex items-center justify-between gap-2">
        <h2 id="home-sources" className="si-h2">Sources</h2>
        <Link href={s.href} className="text-[13px]">Open →</Link>
      </div>
      {s.radars.length === 0 ? (
        <p className="m-0 text-[13px] text-ink-muted">No sources yet. Publishing a Brief starts its radars.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {s.radars.map((r) => {
            const detail = `${fmt(r.records_7d)} ${plural(r.records_7d, 'record', 'records')}, 7 days`;
            return (
              <li key={r.id} data-radar={r.name} data-health={r.health} className="min-w-0">
                <Link href={r.href} className="text-ink no-underline hover:no-underline">
                  {r.health === 'paused' ? (
                    <span className="inline-flex items-center gap-2 text-[13px] text-ink-muted">
                      <span aria-hidden="true" className="h-2.5 w-2.5 flex-none rounded-full border-2 border-line-strong" />
                      <span className="font-semibold">{r.name}</span>
                      <span>Paused</span>
                      <span className="text-[12px]">· {detail}</span>
                    </span>
                  ) : (
                    <HealthIndicator name={r.name} status={r.health} detail={detail} />
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
