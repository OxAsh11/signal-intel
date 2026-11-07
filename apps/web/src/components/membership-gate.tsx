import { redirect } from 'next/navigation';
import { getOrgContext } from '@/lib/org';

/** Signed-in users with no organisation go to onboarding. Renders nothing otherwise. */
export async function MembershipGate() {
  const ctx = await getOrgContext();
  if (ctx && ctx.orgs.length === 0) redirect('/onboarding');
  return null;
}
