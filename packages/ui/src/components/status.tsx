import type { Band, ReplyClass, Stage, Verdict } from '@signal/contracts';
import { cx, slug } from '../cx';

const VERDICT: Record<Verdict, { glyph: string; hint: string }> = {
  Confirmed: { glyph: '●', hint: 'Quoted claim from an accepted source' },
  Likely: { glyph: '◐', hint: 'Supporting claims, not yet the Confirmed rule' },
  Unverified: { glyph: '○', hint: 'No qualifying claim yet' },
  Contradicted: { glyph: '✕', hint: 'An active claim negates the hypothesis' },
};

/** Derived verdict. Never editable: pair with an Explain action. */
export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const v = VERDICT[verdict];
  return (
    <span className={cx('si-chip', `si-verdict-${slug(verdict)}`)} title={v.hint}>
      <span className="si-glyph" aria-hidden="true">
        {v.glyph}
      </span>
      {verdict}
    </span>
  );
}

const BAND_LEVEL: Record<Band, number> = { High: 3, Medium: 2, Low: 1, Excluded: 0 };

export function BandChip({ band, reason }: { band: Band; reason?: string }) {
  const level = BAND_LEVEL[band];
  return (
    <span className={cx('si-chip', 'si-band', `si-band-${slug(band)}`)}>
      {band !== 'Excluded' && (
        <span className="si-pips" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <span key={i} className={cx('si-pip', i < level && 'on')} style={{ height: 5 + i * 3 }} />
          ))}
        </span>
      )}
      {band}
      {band === 'Excluded' && reason ? <span style={{ fontWeight: 400 }}> · {reason}</span> : null}
    </span>
  );
}

export const STAGE_PATH = ['Untouched', 'Queued', 'Contacted', 'Engaged', 'Qualified', 'Meeting', 'Handed off'] as const;

export function StageBadge({ stage, lastStage = 'Contacted' }: { stage: Stage; lastStage?: Stage }) {
  const exit = stage === 'Dormant' || stage === 'Disqualified';
  const idx = STAGE_PATH.indexOf((exit ? lastStage : stage) as (typeof STAGE_PATH)[number]);
  return (
    <span className={cx('si-stage', exit && 'si-stage-exit', exit && `si-stage-${slug(stage)}`)}>
      <span className="si-stage-track" aria-hidden="true">
        {STAGE_PATH.map((s, i) => (
          <span key={s} className={cx('si-stage-step', i <= idx && 'done')} />
        ))}
      </span>
      {stage}
    </span>
  );
}

const REPLY: Record<ReplyClass, string> = {
  Interested: 'interested',
  Referral: 'referral',
  'Not now': 'notnow',
  'Not interested': 'notinterested',
  'Out of office': 'ooo',
  Unsubscribe: 'unsubscribe',
};

export function ReplyClassTag({ replyClass, source }: { replyClass: ReplyClass; source?: 'human' | 'auto' }) {
  return (
    <span className={cx('si-chip', `si-reply-${REPLY[replyClass]}`)}>
      {source === 'human' && (
        <span className="si-glyph" aria-label="Marked by a person" title="Marked by a person">
          ✓
        </span>
      )}
      {replyClass}
    </span>
  );
}

export type HealthStatus = 'ok' | 'degraded' | 'incident' | 'silent';
const HEALTH_LABEL: Record<HealthStatus, string> = { ok: 'Healthy', degraded: 'Degraded', incident: 'Incident', silent: 'Silent' };

/** Silence is failure: `silent` renders as an incident, never as "nothing to see". */
export function HealthIndicator({ name, status, detail }: { name: string; status: HealthStatus; detail?: string }) {
  return (
    <span className={cx('si-health', `si-health-${status}`)}>
      <span className="si-health-mark" aria-hidden="true" />
      <span className="si-health-name">{name}</span>
      <span>{HEALTH_LABEL[status]}</span>
      {detail ? <span className="si-health-detail">· {detail}</span> : null}
    </span>
  );
}
