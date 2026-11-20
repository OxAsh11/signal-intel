import type { Metadata } from 'next';
import { PlaceholderPage } from '@/components/placeholder-page';
import { navItem } from '@/lib/nav';

export const metadata: Metadata = { title: 'Pipeline' };

export default function PipelinePage() {
  return <PlaceholderPage item={navItem('/crm/pipeline')} />;
}
