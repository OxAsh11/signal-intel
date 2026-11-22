import type { Metadata } from 'next';
import { PlaceholderPage } from '@/components/placeholder-page';
import { navItem } from '@/lib/nav';

export const metadata: Metadata = { title: 'Meetings' };

export default function CrmMeetingsPage() {
  return <PlaceholderPage item={navItem('/crm/meetings')} />;
}
