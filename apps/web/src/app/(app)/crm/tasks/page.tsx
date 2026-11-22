import type { Metadata } from 'next';
import { PlaceholderPage } from '@/components/placeholder-page';
import { navItem } from '@/lib/nav';

export const metadata: Metadata = { title: 'Tasks' };

export default function CrmTasksPage() {
  return <PlaceholderPage item={navItem('/crm/tasks')} />;
}
