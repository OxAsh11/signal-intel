import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { getOrgContext } from '@/lib/org';

export const metadata: Metadata = { title: 'People' };

/** The screen lives under the current workspace; this route sends the navigation link there. */
export default async function PeoplePage({ searchParams }: PageProps<'/people'>) {
  const [ctx, params] = await Promise.all([getOrgContext(), searchParams]);
  if (!ctx?.workspace) redirect('/settings');
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    for (const x of Array.isArray(v) ? v : v == null ? [] : [v]) qs.append(k, x);
  }
  redirect(`/workspace/${ctx.workspace.id}/people${qs.size ? `?${qs.toString()}` : ''}`);
}
