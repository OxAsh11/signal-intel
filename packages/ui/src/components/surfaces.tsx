import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from '../cx';

/**
 * Colour families identify kinds of components (light system, Mixpanel guideline gradients):
 * pool = discovery, mint = judgement, grass = engagement, mustard = needs you, lava = cost and alerts.
 */
export type Family = 'pool' | 'mint' | 'grass' | 'mustard' | 'lava';

export interface CardProps extends HTMLAttributes<HTMLElement> {
  family?: Family;
  /** content: grey card with the family's small fade. feature: the family's product-card gradient. */
  kind?: 'content' | 'feature';
  children: ReactNode;
}

export function Card({ family, kind = 'content', className, children, ...rest }: CardProps) {
  const cls = !family
    ? 'si-card'
    : kind === 'feature'
      ? `si-fcard si-fam-${family}`
      : `si-card si-gcard si-fam-${family}`;
  return (
    <section {...rest} className={cx(cls, className)}>
      {children}
    </section>
  );
}

export function FamilyTag({ family, children }: { family: Family; children: ReactNode }) {
  return <span className={`si-ftag si-fam-${family}`}>{children}</span>;
}
