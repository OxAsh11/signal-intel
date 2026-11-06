import type { ButtonHTMLAttributes } from 'react';
import { cx } from '../cx';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'quiet' | 'danger';
  size?: 'md' | 'sm';
  /** Estimated spend, e.g. "≈ $42". Required on any action that spends (cost before action). */
  cost?: string;
}

export function Button({ variant = 'secondary', size = 'md', cost, className, children, type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      {...rest}
      className={cx('si-btn', variant !== 'secondary' && `si-btn-${variant}`, size === 'sm' && 'si-btn-sm', className)}
    >
      {children}
      {cost ? <span className="si-btn-cost">{cost}</span> : null}
    </button>
  );
}

export interface MetricTileProps {
  label: string;
  value: string;
  /** The accounts or events behind the number. Every number links to its rows. */
  href: string;
  version: string;
  delta?: string;
  incomplete?: boolean;
}

export function MetricTile({ label, value, href, version, delta, incomplete }: MetricTileProps) {
  return (
    <a className="si-metric" href={href}>
      <span className="si-label">{label}</span>
      <span className="si-metric-num">{value}</span>
      <span className="si-metric-sub">
        {incomplete ? <span className="si-metric-incomplete">▲ Incomplete period</span> : <span>{delta ?? ''}</span>}
        <span className="si-metric-ver">{version}</span>
      </span>
    </a>
  );
}
