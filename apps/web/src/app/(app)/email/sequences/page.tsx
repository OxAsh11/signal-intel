import type { Metadata } from 'next';
import { PlaceholderPage } from '@/components/placeholder-page';
import { navItem } from '@/lib/nav';

export const metadata: Metadata = { title: 'Sequences' };

export default function SequencesPage() {
  return <PlaceholderPage item={navItem('/email/sequences')} />;
}
