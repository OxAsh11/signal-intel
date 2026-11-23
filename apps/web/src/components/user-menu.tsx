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
  return <span className="min-w-0 truncate text-ink-muted" data-user-menu="">{user.name ?? user.email}</span>;
}
