import type { Metadata } from 'next';
import { PlaceholderPage } from '@/components/placeholder-page';
import { navItem } from '@/lib/nav';

export const metadata: Metadata = { title: 'Inbox' };

export default function EmailInboxPage() {
  return <PlaceholderPage item={navItem('/email/inbox')} />;
}
