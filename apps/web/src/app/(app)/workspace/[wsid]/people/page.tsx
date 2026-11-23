import type { Metadata } from 'next';
import { createClient } from '@/lib/supabase/server';
import { PeopleCards, PeopleTable } from './people-table';
import { PersonPanel } from './person-panel';
import type { PeopleData, PersonRow } from './types';
import type { Column } from '../accounts/_table/types';

export const metadata: Metadata = { title: 'People' };

const waitingText = (n: number) => (n === 1 ? '1 person is waiting in Review' : `${n} people are waiting in Review`);

/**
 * People in the operating table, in person mode (migration 0044). Unlinked people are deliberately
 * absent — they wait in Review until someone links each one to an account. The full email is never
 * returned here: each row carries only its masked hint, and the panel reveals through the worker.
 */
export default async function PeoplePage({ params }: { params: Promise<{ wsid: string }> }) {
  const { wsid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;

  const [tableRes, reviewRes] = await Promise.all([
    supabase.rpc('account_table', {
      p_ws: wsid,
      p_brief: null,
      p_filter: {},
      p_sort: [],
      p_columns: null,
      p_limit: 200,
      p_offset: 0,
      p_entity_type: 'person',
    }),
    supabase
      .from('review_item')
      .select('id', { count: 'exact', head: true })
      .eq('workspace_id', wsid)
      .eq('type', 'unlinked_person')
      .eq('status', 'pending'),
  ]);

  if (tableRes.error) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="si-h1">People</h1>
        <p role="alert" className="m-0 rounded-md border border-danger/40 bg-danger/5 p-4 text-[14px] text-danger">
          People could not be loaded: {tableRes.error.message}
        </p>
      </div>
    );
  }

  const data = tableRes.data as PeopleData | null;
  const rows = (data?.rows ?? []) as PersonRow[];
  const columns = (data?.columns ?? []) as Column[];
  const waiting = reviewRes.count ?? 0;

  return (
    <div className="flex flex-col gap-4">
      <h1 className="si-h1">People</h1>
      {waiting > 0 ? (
        <p className="m-0 text-[13px] text-ink-muted" data-review-waiting="">
          {waitingText(waiting)}. Open Review to link each one to an account.
        </p>
      ) : null}
      {rows.length === 0 ? (
        <div className="rounded-md border border-line bg-surface px-5 py-6 text-[14px] text-ink-body" data-table-state="no-people">
          <p className="m-0 font-medium text-ink">No people found yet</p>
          <p className="mt-1 text-[13px] text-ink-muted">
            {waiting > 0
              ? 'Link the waiting people from Review and they will appear here.'
              : 'Find people from an account’s People tab and they will appear here.'}
          </p>
        </div>
      ) : (
        <>
          <PeopleTable ws={wsid} rows={rows} columns={columns} />
          <PeopleCards ws={wsid} rows={rows} columns={columns} />
          <PersonPanel ws={wsid} />
        </>
      )}
    </div>
  );
}
