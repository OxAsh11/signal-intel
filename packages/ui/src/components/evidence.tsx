'use client';

import { useState, type ReactNode } from 'react';
import type { Band } from '@signal/contracts';
import { cx } from '../cx';
import { BandChip } from './status';

export interface DerivedValueProps {
  label?: string;
  children: ReactNode;
  /** Version stamp, e.g. "brief v14 · rubric v3". */
  versions?: string;
  /** The claims, gates and inputs behind the value. */
  explanation?: ReactNode;
  defaultOpen?: boolean;
}

/** Any computed value. No edit control, ever; disagreement is recorded as feedback. */
export function DerivedValue({ label, children, versions, explanation, defaultOpen = false }: DerivedValueProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <span className="si-derived">
      {label ? <span className="si-label">{label}</span> : null}
      <span className="si-derived-body">
        <span className="si-derived-val">{children}</span>
        <button type="button" className="si-explain" aria-expanded={open} onClick={() => setOpen(!open)}>
          ⓘ Explain
        </button>
      </span>
      {versions ? <span className="si-derived-stamp">{versions}</span> : null}
      {open && explanation ? (
        <div className="si-pop" role="dialog" aria-label="Why this value">
          {explanation}
        </div>
      ) : null}
    </span>
  );
}

export interface EvidenceQuoteProps {
  /** Quoted source text; wrap the exact supporting words in <mark>. */
  children?: ReactNode;
  claim?: string;
  sourceType: string;
  sourceName: string;
  /** Link to the stored source record, never the live page. */
  sourceHref?: string;
  observed: string;
  expired?: boolean;
  translated?: string;
}

export function EvidenceQuote(p: EvidenceQuoteProps) {
  return (
    <figure className="si-quote" style={{ margin: 0 }}>
      <span className="si-quote-rule" aria-hidden="true" />
      <div>
        <blockquote className="si-quote-text" style={{ margin: 0 }}>
          {p.children ?? <span className="si-muted">No quote: structured source</span>}
        </blockquote>
        <figcaption className="si-quote-meta">
          {p.claim ? <span className="si-quote-claim">{p.claim}</span> : null}
          <span>{p.sourceType}</span>
          {p.sourceHref ? <a href={p.sourceHref}>{p.sourceName}</a> : <span>{p.sourceName}</span>}
          <span className={p.expired ? 'si-quote-expired' : undefined}>
            {p.expired ? `Expired · observed ${p.observed}` : `Observed ${p.observed}`}
          </span>
          {p.translated ? <span>Translated from {p.translated}</span> : null}
        </figcaption>
      </div>
    </figure>
  );
}

export interface ScoreBreakdownProps {
  priority: number;
  band: Band;
  dimensions: { name: 'Fit' | 'Evidence' | 'Scale' | 'Timing'; value: number }[];
  readiness: number;
  gates?: { name: string; hit: boolean }[];
  version?: string;
}

function DimRow({ name, value, readiness }: { name: string; value: number; readiness?: boolean }) {
  return (
    <div className={cx('si-dim', readiness && 'si-dim-readiness')}>
      <span>{name}</span>
      <span className="si-dim-track" role="img" aria-label={`${name} ${value} of 100`}>
        <span className="si-dim-fill" style={{ width: `${value}%`, display: 'block' }} />
      </span>
      <span className="si-dim-num">{value}</span>
    </div>
  );
}

export function ScoreBreakdown({ priority, band, dimensions, readiness, gates, version }: ScoreBreakdownProps) {
  return (
    <section className="si-score">
      <div className="si-score-head">
        <div>
          <div className="si-label">Priority</div>
          <div className="si-score-big">{priority}</div>
        </div>
        <BandChip band={band} />
      </div>
      {dimensions.map((d) => (
        <DimRow key={d.name} name={d.name} value={d.value} />
      ))}
      <div className="si-score-sep" />
      <DimRow name="Readiness" value={readiness} readiness />
      {gates?.length ? (
        <div className="si-gates">
          {gates.map((g) => (
            <span key={g.name} className={cx('si-gate', g.hit && 'si-gate-hit')}>
              {g.hit ? '✕' : '✓'} {g.name}
            </span>
          ))}
        </div>
      ) : null}
      {version ? <div className="si-derived-stamp">{version}</div> : null}
    </section>
  );
}
