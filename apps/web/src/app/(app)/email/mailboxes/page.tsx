import type { Metadata } from 'next';
import { PlaceholderPage } from '@/components/placeholder-page';
import { navItem } from '@/lib/nav';

export const metadata: Metadata = { title: 'Mailboxes' };

export default function MailboxesPage() {
  return <PlaceholderPage item={navItem('/email/mailboxes')} />;
}
