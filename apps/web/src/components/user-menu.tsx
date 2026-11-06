import Link from 'next/link';
import { getCurrentUser } from '@/lib/supabase/server';

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
  return <span className="text-ink-muted">{user.name ?? user.email}</span>;
}
