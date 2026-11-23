import type { CellData, Column } from '../accounts/_table/types';

/** The person-mode shape of public.account_table (migration 0044). */
export type PersonRow = {
  account_id: string;
  entity_id: string;
  name: string;
  country: string | null;
  domain: string | null;
  verdict: string | null;
  band: string | null;
  priority: number | null;
  stage: string;
  owner: { member_id: string; name: string } | null;
  last_signal: string | null;
  account_name: string | null;
  account_entity_id: string | null;
  title: string | null;
  work_email: string | null;
  linkedin: string | null;
  location_mismatch: boolean;
  cells: Record<string, CellData | undefined>;
};

export type PeopleData = { total: number; rows: PersonRow[]; columns: Column[] };
