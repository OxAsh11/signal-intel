import type { Metadata } from 'next';
import { PlaceholderPage } from '@/components/placeholder-page';
import { navItem } from '@/lib/nav';

export const metadata: Metadata = { title: 'People' };

export default function PeoplePage() {
  return <PlaceholderPage item={navItem('/people')} />;
}
