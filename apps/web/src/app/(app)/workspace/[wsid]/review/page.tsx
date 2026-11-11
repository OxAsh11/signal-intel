import { createClient } from '@/lib/supabase/server';
import { mergeAction, skipAction, createNewAction } from './actions';

type ReviewRow = {
  id: string;
  match_method: string | null;
  match_confidence: number | null;
  created_at: string;
  mention: {
    raw_name: string | null;
    raw_domain: string | null;
    raw_country: string | null;
    entity_type: string | null;
  } | null;
  entity: {
    display_name: string | null;
  } | null;
};

export default async function ReviewPage({
  params,
}: {
  params: Promise<{ wsid: string }>;
}) {
  const { wsid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;

  const { data, error } = await supabase
    .from('review_item')
    .select(
      `id, match_method, match_confidence, created_at,
       mention:mention_id ( raw_name, raw_domain, raw_country, entity_type ),
       entity:candidate_entity ( display_name )`
    )
    .eq('workspace_id', wsid)
    .eq('status', 'pending')
    .order('created_at', { ascending: true })
    .limit(50);

  if (error) {
    return (
      <div>
        <h1>Review Queue</h1>
        <p>Failed to load review queue: {error.message}</p>
      </div>
    );
  }

  const rows = (data ?? []) as unknown as ReviewRow[];

  return (
    <div>
      <h1>Review Queue</h1>
      {rows.length === 0 ? (
        <p>No items to review.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Mention</th>
              <th>Candidate</th>
              <th>Method</th>
              <th>Confidence</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => {
              const mention = item.mention;
              const candidate = item.entity;
              const confidence =
                item.match_confidence != null ? `${item.match_confidence}%` : '—';
              const method = item.match_method ?? '—';

              const boundMerge = mergeAction.bind(null, item.id, wsid);
              const boundSkip = skipAction.bind(null, item.id, wsid);
              const boundCreateNew = createNewAction.bind(null, item.id, wsid);

              return (
                <tr key={item.id}>
                  <td>
                    {mention?.raw_name ?? '—'}
                    {mention?.raw_domain ? ` (${mention.raw_domain})` : ''}
                    {mention?.raw_country ? `, ${mention.raw_country}` : ''}
                    {mention?.entity_type ? ` · ${mention.entity_type}` : ''}
                  </td>
                  <td>{candidate?.display_name ?? '—'}</td>
                  <td>{method}</td>
                  <td>{confidence}</td>
                  <td>
                    <form action={boundMerge} style={{ display: 'inline' }}>
                      <button type="submit">Merge</button>
                    </form>{' '}
                    <form action={boundSkip} style={{ display: 'inline' }}>
                      <button type="submit">Skip</button>
                    </form>{' '}
                    <form action={boundCreateNew} style={{ display: 'inline-flex', gap: '4px' }}>
                      <input
                        type="text"
                        name="new_name"
                        placeholder="New entity name"
                        required
                      />
                      <button type="submit">Create New</button>
                    </form>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
