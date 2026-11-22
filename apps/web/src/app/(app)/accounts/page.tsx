import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { getOrgContext } from '@/lib/org';

export const metadata: Metadata = { title: 'Accounts' };

/**
 * The screen lives under the current workspace; this route sends the navigation link there with
 * its query intact, so the filters Home links carry (?view=, ?verdict=, ?radar=, ?stage=,
 * ?brief=) reach the table that honours them.
 */
export default async function AccountsPage({ searchParams }: PageProps<'/accounts'>) {
  const [ctx, params] = await Promise.all([getOrgContext(), searchParams]);
  if (!ctx?.workspace) redirect('/settings');
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    for (const x of Array.isArray(v) ? v : v == null ? [] : [v]) qs.append(k, x);
  }
  redirect(`/workspace/${ctx.workspace.id}/accounts${qs.size ? `?${qs.toString()}` : ''}`);
}
