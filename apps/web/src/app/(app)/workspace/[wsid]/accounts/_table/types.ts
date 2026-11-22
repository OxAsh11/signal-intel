/** The jsonb that public.account_table returns (supabase/migrations/20261121000043_operating_table.sql). */
export type CellState = 'empty' | 'queued' | 'running' | 'value' | 'derived' | 'no_data' | 'skipped' | 'error' | 'stale';

export type ValueType = 'boolean' | 'number' | 'text' | 'date' | 'enum' | 'entity_ref';

export type CellData = {
  state: CellState;
  value: unknown;
  confidence: number | null;
  claim_id: string | null;
  run_id: string | null;
  error: string | null;
};

export type Column = {
  id: string;
  key: string;
  label: string;
  value_type: ValueType | string;
  fill_method: string | null;
  auto_run: boolean | null;
};

export type TableRow = {
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
  cells: Record<string, CellData | undefined>;
};

export type TableData = { total: number; rows: TableRow[]; columns: Column[] };
