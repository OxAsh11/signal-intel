import type { Metadata } from 'next';
import { PlaceholderPage } from '@/components/placeholder-page';
import { navItem } from '@/lib/nav';

export const metadata: Metadata = { title: 'Approvals' };

export default function ApprovalsPage() {
  return <PlaceholderPage item={navItem('/email/approvals')} />;
}
