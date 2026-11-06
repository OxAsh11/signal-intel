import type * as React from 'react';

export type Verdict = 'Confirmed' | 'Likely' | 'Unverified' | 'Contradicted';
export type Band = 'High' | 'Medium' | 'Low' | 'Excluded';
export type Stage = 'Untouched' | 'Queued' | 'Contacted' | 'Engaged' | 'Qualified' | 'Meeting' | 'Handed off' | 'Dormant' | 'Disqualified';
export type ReplyClass = 'Interested' | 'Referral' | 'Not now' | 'Not interested' | 'Out of office' | 'Unsubscribe';
export type EditTier = 'Narrow' | 'Redefine' | 'Expand';
export type PushMode = 'off' | 'dry run' | 'live';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'quiet' | 'danger';
  size?: 'md' | 'sm';
  /** Estimated spend, shown inside the button, e.g. "≈ $42". Required on any action that spends. */
  cost?: string;
}
export declare function Button(props: ButtonProps): React.ReactElement;

export interface VerdictBadgeProps { verdict: Verdict }
export declare function VerdictBadge(props: VerdictBadgeProps): React.ReactElement;

export interface BandChipProps { band: Band; /** Gate that excluded the account, e.g. "Competitor". */ reason?: string }
export declare function BandChip(props: BandChipProps): React.ReactElement;

export interface StageBadgeProps { stage: Stage; /** For Dormant or Disqualified: the stage it left from. */ lastStage?: Stage }
export declare function StageBadge(props: StageBadgeProps): React.ReactElement;

export interface ReplyClassTagProps { replyClass: ReplyClass; /** "human" shows a check: a person's mark always wins. */ source?: 'human' | 'auto' }
export declare function ReplyClassTag(props: ReplyClassTagProps): React.ReactElement;

export interface DerivedValueProps {
  label?: string;
  children: React.ReactNode;
  /** Version stamp, e.g. "brief v14 · rubric v3". */
  versions?: string;
  /** Content of the Explain panel: the claims, gates and versions behind the value. */
  explanation?: React.ReactNode;
  defaultOpen?: boolean;
}
export declare function DerivedValue(props: DerivedValueProps): React.ReactElement;

export interface EvidenceQuoteProps {
  /** The quoted source text. Wrap the exact supporting words in <mark>. */
  children: React.ReactNode;
  claim?: string;
  sourceType: string;
  sourceName: string;
  sourceHref?: string;
  observed: string;
  expired?: boolean;
  /** Original language when the text shown is a translation. */
  translated?: string;
}
export declare function EvidenceQuote(props: EvidenceQuoteProps): React.ReactElement;

export interface ScoreBreakdownProps {
  priority: number;
  band: Band;
  /** Fit, Evidence, Scale, Timing in that order, each 0–100. */
  dimensions: { name: 'Fit' | 'Evidence' | 'Scale' | 'Timing'; value: number }[];
  /** Reachability, 0–100. Shown apart from priority. */
  readiness: number;
  gates?: { name: string; hit: boolean }[];
  version?: string;
}
export declare function ScoreBreakdown(props: ScoreBreakdownProps): React.ReactElement;

export interface CostPreviewProps {
  title?: string;
  tier: EditTier;
  entering: string | number;
  leaving: string | number;
  cost: string;
  duration: string;
  needsApproval?: boolean;
  budgetLeft?: string;
  onRun?: () => void;
  onCancel?: () => void;
}
export declare function CostPreview(props: CostPreviewProps): React.ReactElement;

export interface PushModeSwitchProps { value?: PushMode; onChange?: (mode: PushMode) => void }
export declare function PushModeSwitch(props: PushModeSwitchProps): React.ReactElement;

export interface MetricTileProps {
  label: string;
  value: string;
  /** Link to the accounts or events behind the number. Required in the app. */
  href: string;
  delta?: string;
  /** Metric definition version, e.g. "def v2". */
  version: string;
  /** The period had a health incident (M17 data completeness). */
  incomplete?: boolean;
}
export declare function MetricTile(props: MetricTileProps): React.ReactElement;

export interface HealthIndicatorProps { name: string; status: 'ok' | 'degraded' | 'incident' | 'silent'; detail?: string }
export declare function HealthIndicator(props: HealthIndicatorProps): React.ReactElement;

export interface ReviewQueueItemProps {
  kind?: string;
  confidence: number;
  waiting?: string;
  /** Two candidates. Each field is [label, value, differs?]. */
  candidates: { name: string; fields: [string, string, boolean?][] }[];
}
export declare function ReviewQueueItem(props: ReviewQueueItemProps): React.ReactElement;

declare global {
  interface Window {
    SignalIntel: {
      Button: typeof Button; VerdictBadge: typeof VerdictBadge; BandChip: typeof BandChip; StageBadge: typeof StageBadge;
      ReplyClassTag: typeof ReplyClassTag; DerivedValue: typeof DerivedValue; EvidenceQuote: typeof EvidenceQuote;
      ScoreBreakdown: typeof ScoreBreakdown; CostPreview: typeof CostPreview; PushModeSwitch: typeof PushModeSwitch;
      MetricTile: typeof MetricTile; HealthIndicator: typeof HealthIndicator; ReviewQueueItem: typeof ReviewQueueItem;
    };
  }
}
