export type ValueType = 'boolean' | 'number' | 'enum' | 'text' | 'date';
export type Role = 'signal' | 'fit.vertical' | 'fit.geography' | 'fit.size' | 'fit.commercial_role' | 'scale' | 'info';
export type RuleOp = 'is_true' | 'is_false' | 'present' | 'at_least' | 'one_of' | 'contains';

/** One column the AI fills for every company (an item of `config.predicates`). */
export type ColumnSpec = {
  key: string;
  label: string;
  value_type: ValueType;
  allowed_values: string[] | null;
  extraction_hint: string;
  decay_half_life_days: number | null;
  role: Role;
};

/** A rule the verdict is decided by (an item of `config.evidence_rules`). */
export type EvidenceRule = {
  predicate: string;
  kind: 'requires' | 'disqualifies';
  op: RuleOp;
  value: string[] | number | null;
  min_sources: number;
  max_age_days: number | null;
};

export type BriefConfig = {
  offer?: string;
  hypothesis?: string;
  examples?: { positive?: string[]; negative?: string[] };
  sources?: { radar_types?: string[] };
  icp?: { verticals?: string[]; geographies?: string[]; size_bands?: string[]; commercial_roles?: string[] };
  personas?: { buyer?: string[]; user?: string[]; influencer?: string[] };
  /** Column specs; legacy configs hold plain keys ("a key with no spec"). */
  predicates?: (ColumnSpec | string)[];
  evidence_rules?: EvidenceRule[];
  /** What the AI assumed while drafting; read-only. */
  assumptions?: string[];
  seeds?: { company_names?: string[]; company_domains?: string[] };
  research_template?: Record<string, unknown>;
  scoring?: Record<string, unknown>;
  budget?: { day_cap?: number | null; month_cap?: number | null };
};

export const MAX_COLUMNS = 12;
export const MAX_RULES = 10;

export const ROLE_LABELS: Record<Role, string> = {
  signal: 'Signal (decides the verdict)',
  'fit.vertical': 'Fit: industry',
  'fit.geography': 'Fit: country or region',
  'fit.size': 'Fit: size',
  'fit.commercial_role': 'Fit: commercial role',
  scale: 'Scale',
  info: 'Information only',
};

export const TYPE_LABELS: Record<ValueType, string> = {
  boolean: 'Yes or no',
  number: 'Number',
  enum: 'One of a list',
  text: 'Text',
  date: 'Date',
};

/** The rule ops each column type allows (Global Constraints). */
export const OPS_BY_TYPE: Record<ValueType, RuleOp[]> = {
  boolean: ['is_true', 'is_false', 'present'],
  number: ['at_least', 'present'],
  enum: ['one_of', 'present'],
  text: ['contains', 'present'],
  date: ['present'],
};

export const OP_LABELS: Record<RuleOp, string> = {
  is_true: 'is yes',
  is_false: 'is no',
  present: 'has any value',
  at_least: 'is at least',
  one_of: 'is one of',
  contains: 'contains',
};

export function isColumnSpec(p: ColumnSpec | string): p is ColumnSpec {
  return typeof p === 'object' && p !== null;
}

/** The Brief's column specs, skipping legacy plain keys. */
export function columnSpecs(cfg: BriefConfig | Record<string, unknown> | null | undefined): ColumnSpec[] {
  const preds = (cfg as BriefConfig | null | undefined)?.predicates;
  return Array.isArray(preds) ? preds.filter(isColumnSpec) : [];
}

const asList = (v: EvidenceRule['value']) => (Array.isArray(v) ? v.join(', ') : '');

/**
 * A rule as a sentence, e.g.
 * `Requires "Runs or plans a drone programme" = yes, from 1 source, in the last 730 days`.
 */
export function describeRule(rule: EvidenceRule, columns: (ColumnSpec | string)[]): string {
  const col = columns.find((c) => (isColumnSpec(c) ? c.key : c) === rule.predicate);
  const label = `"${col && isColumnSpec(col) ? col.label : rule.predicate}"`;
  const what: Record<RuleOp, string> = {
    is_true: `${label} = yes`,
    is_false: `${label} = no`,
    present: `${label} has any value`,
    at_least: `${label} is at least ${typeof rule.value === 'number' ? rule.value : '?'}`,
    one_of: `${label} is one of: ${asList(rule.value)}`,
    contains: `${label} contains: ${asList(rule.value)}`,
  };
  const n = rule.min_sources ?? 1;
  const sources = `from ${n} ${n === 1 ? 'source' : 'sources'}`;
  const parts = rule.kind === 'requires' ? [`Requires ${what[rule.op] ?? label}`, sources] : [`Rules out when ${what[rule.op] ?? label}`];
  if (rule.kind !== 'requires' && n > 1) parts.push(sources);
  if (rule.max_age_days) parts.push(`in the last ${rule.max_age_days} days`);
  return parts.join(', ');
}

/** A column key from its label: lower snake case, at most 40 characters, `c_` first if it starts with a digit. */
export function keyFromLabel(label: string): string {
  let key = label
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  if (/^[0-9]/.test(key)) key = `c_${key}`;
  return key.slice(0, 40).replace(/_+$/, '');
}

/** A short Brief name from the description's first sentence; the person can rename it later. */
export function nameFromDescription(description: string): string {
  const first = (description.split(/(?<=[.!?])\s/)[0] ?? '').replace(/\s+/g, ' ').trim();
  return first.length > 60 ? `${first.slice(0, 57).replace(/\s+\S*$/, '')}…` : first;
}
