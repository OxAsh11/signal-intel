import type { Metadata } from 'next';
import { PlaceholderPage } from '@/components/placeholder-page';
import { navItem } from '@/lib/nav';

export const metadata: Metadata = { title: 'Email engine' };

export default function EmailEnginePage() {
  return <PlaceholderPage item={navItem('/email')} />;
}
