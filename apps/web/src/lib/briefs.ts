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
