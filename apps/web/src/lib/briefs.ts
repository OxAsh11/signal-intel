export type ValueType = 'boolean' | 'number' | 'enum' | 'text' | 'date';
export type Role = 'signal' | 'fit.vertical' | 'fit.geography' | 'fit.size' | 'fit.commercial_role' | 'scale' | 'info';
export type RuleOp = 'is_true' | 'is_false' | 'present' | 'at_least' | 'one_of' | 'contains';
/** How a column is filled: found in the sources by the AI, or computed by a formula. */
export type FillMethod = 'extract' | 'formula';

/** One column the AI fills for every company (an item of `config.predicates`). */
export type ColumnSpec = {
  key: string;
  label: string;
  value_type: ValueType;
  allowed_values: string[] | null;
  extraction_hint: string;
  decay_half_life_days: number | null;
  role: Role;
  /** Absent means `extract`. */
  fill_method?: FillMethod;
  /** The expression of a formula column (1–500 characters); null otherwise. */
  formula?: string | null;
  /** Formula only: the column keys the formula reads, from `references()`. */
  inputs?: string[];
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

export const FILL_LABELS: Record<FillMethod, string> = {
  extract: 'Found in sources',
  formula: 'Formula',
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

/** A column computed by a formula: it has no hint and no evidence rule. */
export function isFormulaColumn(p: ColumnSpec | string): p is ColumnSpec & { fill_method: 'formula' } {
  return isColumnSpec(p) && p.fill_method === 'formula';
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

/** A column key (Global Constraints); the database refuses any other. */
export const KEY_RE = /^[a-z][a-z0-9_]{1,39}$/;

/**
 * A column key from its label: lower snake case, at most 40 characters, `c_` first if it starts
 * with a digit. A label that gives no usable key (empty, one letter, no Latin letters) gives
 * `c_<n>`, n being the column's number.
 */
export function keyFromLabel(label: string, n = 1): string {
  let key = label
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  if (/^[0-9]/.test(key)) key = `c_${key}`;
  key = key.slice(0, 40).replace(/_+$/, '');
  return KEY_RE.test(key) ? key : `c_${n}`;
}

/** A predicate of the Brief's workspace, as the Columns editor reads it. */
export type WorkspacePredicate = {
  key: string;
  label: string;
  value_type: string;
  allowed_values: unknown;
  extraction_hint: string | null;
  decay_half_life_days: number | null;
  /** How the workspace fills it; absent means `extract`. */
  fill_method?: string | null;
};

/** The scoring role a legacy key had before Briefs had column specs (scoring_core.LEGACY_ROLES). */
export const LEGACY_ROLES: Record<string, Role> = {
  vertical: 'fit.vertical',
  size: 'fit.size',
  geography: 'fit.geography',
  commercial_role: 'fit.commercial_role',
  sites: 'scale',
  seats: 'scale',
  units: 'scale',
};

export const LEGACY_SEED_NOTICE =
  "This Brief used every workspace column. They are listed so nothing stops being collected; remove any you don't need.";

/**
 * A Brief without column specs collects every workspace column. Its Columns editor starts from
 * those columns, so adding one never quietly stops the others being collected: same key, label,
 * type, values, hint and decay, the role a legacy key had (else "info"), at most 12. A predicate
 * that cannot be a column (another type, a key or value list a column cannot have) is left out.
 */
export function columnsFromWorkspace(preds: WorkspacePredicate[]): ColumnSpec[] {
  const out: ColumnSpec[] = [];
  for (const p of preds) {
    if (out.length >= MAX_COLUMNS) break;
    if (!KEY_RE.test(p.key) || !(p.value_type in TYPE_LABELS)) continue;
    // A legacy Brief collected only extracted columns; a formula column is never seeded as one.
    if ((p.fill_method ?? 'extract') !== 'extract') continue;
    const value_type = p.value_type as ValueType;
    let allowed_values: string[] | null = null;
    if (value_type === 'enum') {
      const vals = Array.isArray(p.allowed_values)
        ? [...new Set(p.allowed_values.filter((v): v is string => typeof v === 'string' && v.trim() !== ''))]
        : [];
      if (vals.length < 2) continue;
      allowed_values = vals.slice(0, 12); // publishing adds values to the predicate, never removes them
    }
    const decay = p.decay_half_life_days;
    out.push({
      key: p.key,
      label: (p.label?.trim() || p.key).slice(0, 80),
      value_type,
      allowed_values,
      extraction_hint: (p.extraction_hint ?? '').slice(0, 300),
      decay_half_life_days: decay && Number.isInteger(decay) && decay >= 1 ? Math.min(decay, 3650) : null,
      role: LEGACY_ROLES[p.key] ?? 'info',
    });
  }
  return out;
}

/**
 * What is wrong with a column's key, in plain words, or null. `existing` is the workspace column
 * with this key, when the key can still change (it follows the label): its type and fill method
 * stay, so the column must match them.
 */
export function columnKeyProblem(
  col: ColumnSpec,
  others: ColumnSpec[],
  existing: { value_type: string; fill_method?: string | null } | undefined,
): string | null {
  const existingType = existing?.value_type;
  if (!KEY_RE.test(col.key)) {
    return 'The key must be 2 to 40 lowercase letters, digits or underscores, starting with a letter. Change the label.';
  }
  if (others.some((o) => o.key === col.key)) return 'Another column of this Brief has the same key. Change the label.';
  if (existingType && existingType !== col.value_type) {
    const type = TYPE_LABELS[existingType as ValueType] ?? existingType;
    return `The workspace already has a column "${col.key}" of type ${type}. Change the label for another key, or set the type to ${type}.`;
  }
  const fill = (existing?.fill_method ?? 'extract') as FillMethod;
  if (existing && fill !== (col.fill_method ?? 'extract')) {
    const how = FILL_LABELS[fill] ?? fill;
    return `The workspace already has a column "${col.key}" filled by ${how}. Change the label for another key, or set Filled by to ${how}.`;
  }
  return null;
}

/** A "newer than" number of days from its input: blank is any age; anything else must be 1 to 3650 whole days. */
export function parseDays(text: string, badInput = false): { value: number | null; error: string | null } {
  const ERROR = 'Use a whole number of days from 1 to 3650, or leave it blank for any age.';
  if (badInput) return { value: null, error: ERROR };
  if (text.trim() === '') return { value: null, error: null };
  const n = Number(text);
  return Number.isInteger(n) && n >= 1 && n <= 3650 ? { value: n, error: null } : { value: null, error: ERROR };
}

/** A short Brief name from the description's first sentence; the person can rename it later. */
export function nameFromDescription(description: string): string {
  const first = (description.split(/(?<=[.!?])\s/)[0] ?? '').replace(/\s+/g, ' ').trim();
  return first.length > 60 ? `${first.slice(0, 57).replace(/\s+\S*$/, '')}…` : first;
}
