import type { Metadata } from 'next';
import { PlaceholderPage } from '@/components/placeholder-page';
import { navItem } from '@/lib/nav';

export const metadata: Metadata = { title: 'CRM' };

export default function CrmPage() {
  return <PlaceholderPage item={navItem('/crm')} />;
}
