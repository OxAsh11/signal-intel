import { loadAccountTags } from '@/app/actions/tags';
import { AccountsTable } from './table';
import type { AccountsTableProps } from './table';

/** AccountsTable plus the hand-label chips, fetched from the client action once on mount. */
export async function AccountsTableWithTags(props: Omit<AccountsTableProps, 'tagsByAccount'>) {
  const tags = await loadAccountTags(props.ws);
  const names = new Map<string, { id: string; name: string; colour: string }[]>();
  for (const t of tags) {
    if (!t.tag_name) continue;
    const row = names.get(t.account_id) ?? [];
    row.push({ id: t.tag_id, name: t.tag_name, colour: '#6b7280' });
    names.set(t.account_id, row);
  }
  const tagsByAccount: Record<string, { id: string; name: string; colour: string }[]> = {};
  for (const row of props.rows) tagsByAccount[row.entity_id] = names.get(row.account_id) ?? [];
  return <AccountsTable {...props} tagsByAccount={tagsByAccount} />;
}