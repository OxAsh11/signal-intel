/** Radar types (M2): the connector a radar runs on, with the label people see. */
export const RADAR_TYPES = [
  { value: 'news', label: 'News & publications' },
  { value: 'job_post', label: 'Job postings' },
  { value: 'registry', label: 'Public registries & permits' },
  { value: 'tender', label: 'Tenders & procurement' },
  { value: 'event', label: 'Events (speakers & exhibitors)' },
  { value: 'directory', label: 'Directories & partner lists' },
  { value: 'social_post', label: 'Public social posts' },
  { value: 'website', label: 'Company websites' },
  { value: 'csv_upload', label: 'CSV upload' },
] as const;

/** Types found through web search (Brave); they accept an optional list of sites. */
export const WEB_SEARCH_TYPES: ReadonlySet<string> = new Set([
  'registry',
  'tender',
  'event',
  'directory',
  'social_post',
]);

export function radarTypeLabel(type: string): string {
  return RADAR_TYPES.find((t) => t.value === type)?.label ?? type.replace(/_/g, ' ');
}

/** Non-empty, trimmed lines of a textarea. */
export function lines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
}

/**
 * Yield as "N (P%)": P is the share of records that reached the relevance judge (not
 * pre-filtered) and led to a Confirmed or Likely account.
 */
export function yieldLabel(records: number, prefiltered: number, yielded: number): string {
  const judged = records - prefiltered;
  if (judged <= 0) return `${yielded} (—)`;
  return `${yielded} (${Math.round((yielded / judged) * 100)}%)`;
}

/** The worker marks a radar this way when the Brave Search key is missing. */
export function isNotSetUp(error: string | null | undefined): boolean {
  return Boolean(error && /not set up yet/i.test(error));
}
