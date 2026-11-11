import type { Metadata } from 'next';
import { redirect } from 'next/navigation';
import { getOrgContext } from '@/lib/org';

export const metadata: Metadata = { title: 'Meetings and Tasks' };

/** The screen lives under the current workspace; this route sends the navigation link there. */
export default async function MeetingsPage() {
  const ctx = await getOrgContext();
  if (!ctx?.workspace) redirect('/settings');
  redirect(`/workspace/${ctx.workspace.id}/meetings`);
}
