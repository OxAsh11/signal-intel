import type { Metadata } from 'next';
import { ScreenPlaceholder } from '@/components/screen-placeholder';
import { screen } from '@/lib/screens';

export const metadata: Metadata = { title: 'Inbox' };

export default function InboxPage() {
  return <ScreenPlaceholder screen={screen('/inbox')} />;
}
