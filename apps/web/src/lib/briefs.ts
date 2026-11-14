export type BriefConfig = {
  offer?: string;
  hypothesis?: string;
  examples?: { positive?: string[]; negative?: string[] };
  sources?: { radar_types?: string[] };
  icp?: { verticals?: string[]; geographies?: string[]; size_bands?: string[]; commercial_roles?: string[] };
  personas?: { buyer?: string[]; user?: string[]; influencer?: string[] };
  predicates?: string[];
  seeds?: { company_names?: string[]; company_domains?: string[] };
  research_template?: Record<string, unknown>;
  scoring?: Record<string, unknown>;
  budget?: { day_cap?: number | null; month_cap?: number | null };
};

/** A short Brief name from the description's first sentence; the person can rename it later. */
export function nameFromDescription(description: string): string {
  const first = (description.split(/(?<=[.!?])\s/)[0] ?? '').replace(/\s+/g, ' ').trim();
  return first.length > 60 ? `${first.slice(0, 57).replace(/\s+\S*$/, '')}…` : first;
}
