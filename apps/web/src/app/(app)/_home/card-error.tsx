import { Card, type Family } from '@signal/ui';

/** A card whose RPC failed: its title, and a Retry link to the same URL. */
export function CardError({ id, title, retry, family }: { id: string; title: string; retry: string; family?: Family }) {
  return (
    <Card family={family} aria-labelledby={id} data-card-error="">
      <h2 id={id} className="si-h2">{title}</h2>
      <p className="m-0 text-[13px] text-ink-body">
        This card could not load.{' '}
        <a href={retry} className="text-ink underline">Retry</a>
      </p>
    </Card>
  );
}

/** The fallback while a card's RPC is in flight. */
export function CardSkeleton({ title, lines = 3 }: { title: string; lines?: number }) {
  return (
    <div className="si-card" aria-busy="true" aria-label={`${title}, loading`}>
      <span className="si-label">{title}</span>
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="block h-3 animate-pulse rounded-[2px] bg-surface-sunken" style={{ width: `${88 - i * 14}%` }} />
      ))}
    </div>
  );
}
