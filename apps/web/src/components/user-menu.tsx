import Link from 'next/link';
import { getCurrentUser } from '@/lib/supabase/server';
import { UserDropdown } from './user-dropdown';

/** Reads the session, so it always renders inside a <Suspense> boundary. */
export async function UserMenu() {
  const user = await getCurrentUser();
  if (!user) {
    return (
      <Link href="/login" className="text-signal">
        Sign in
      </Link>
    );
  }
  return <UserDropdown email={user.email} name={user.name} />;
}
